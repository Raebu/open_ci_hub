"""Isolated, network-free acceptance tests for MCP Events bidirectional delivery."""
import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import direct_pc_mcp as mcp
import hub_dialogue_events as dialogue


class DialogueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        state = Path(self.temporary.name) / "private-dialogue"
        self.state_patch = patch.object(dialogue, "STATE", state)
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)
        self.db_patch = patch.object(dialogue, "DB", state / "dialogue.sqlite3")
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.enabled_patch = patch.object(dialogue, "ENABLED", state / "owner-enabled")
        self.enabled_patch.start()
        self.addCleanup(self.enabled_patch.stop)
        self.secret = "whsec_" + base64.b64encode(b"x" * 32).decode()
        self.url = "https://notify.chatgpt.com/events"
        self.params = {"name": dialogue.EVENT_NAME, "arguments": {"channel": "operations"},
                       "delivery": {"mode": "webhook", "url": self.url, "secret": self.secret}}

    def call(self, method, params=None):
        return mcp.mcp_response({"jsonrpc": "2.0", "id": 2, "method": method, "params": params or {}})

    def test_protocol_v2_discovery_and_v1_compatibility(self):
        self.assertEqual(self.call("server/discover")["result"]["supportedVersions"], ["2026-07-28"])
        self.assertIn("events", self.call("server/discover")["result"]["capabilities"])
        self.assertEqual(self.call("initialize")["result"]["protocolVersion"], "2025-06-18")
        names = [t["name"] for t in self.call("tools/list")["result"]["tools"]]
        self.assertIn("hub_dialogue_reply", names)
        self.assertEqual(self.call("events/list")["result"]["events"][0]["name"], dialogue.EVENT_NAME)

    def test_owner_opt_in_fail_closed(self):
        self.assertEqual(self.call("events/subscribe", self.params)["error"]["code"], -32602)
        with self.assertRaises(PermissionError):
            dialogue.queue_message("operations", "Hello")
        with self.assertRaises(PermissionError):
            dialogue.set_enabled(True)
        dialogue.set_enabled(True, confirmed=True)
        self.assertTrue(dialogue.ENABLED.is_file())
        self.assertEqual(dialogue.ENABLED.stat().st_mode & 0o777, 0o600)
        dialogue.set_enabled(False, confirmed=True)
        self.assertFalse(dialogue.ENABLED.exists())

    def test_https_callback_validation_rejects_ssrf(self):
        for url in ("http://notify.chatgpt.com/a", "https://127.0.0.1/x",
                    "https://notify.chatgpt.com:8080/a", "https://other.example.org/a",
                    "https://notify.chatgpt.com@127.0.0.1/a"):
            with self.subTest(url=url), self.assertRaises(dialogue.CallbackError):
                dialogue._destination(url)
        with patch("hub_dialogue_events.socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("127.0.0.1", 443))]):
            with self.assertRaises(dialogue.CallbackError):
                dialogue._destination(self.url)
        with patch("hub_dialogue_events.socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("8.8.8.8", 443))]):
            parsed, ip = dialogue._destination(self.url)
        self.assertEqual(ip, "8.8.8.8")
        self.assertEqual(parsed.path, "/events")

    def test_signature_matches_standard_webhooks(self):
        import hashlib
        import hmac
        body = b'{"x":1}'
        headers = dialogue._signed_headers(self.secret, "evt_123", body, "sub_123")
        signed = b"evt_123." + headers["webhook-timestamp"].encode() + b"." + body
        expect = "v1," + base64.b64encode(hmac.new(b"x"*32, signed, hashlib.sha256).digest()).decode()
        self.assertEqual(headers["webhook-signature"], expect)
        self.assertEqual(headers["X-MCP-Subscription-Id"], "sub_123")

    def test_subscribe_verify_deliver_reply_unsubscribe(self):
        dialogue.set_enabled(True, confirmed=True)
        calls = []
        def fake_post(url, secret, sid, eid, payload):
            calls.append((eid, payload))
            if payload.get("type") == "verification":
                return 200, json.dumps({"challenge": payload["challenge"]}).encode()
            return 202, b""
        with patch("hub_dialogue_events._destination", return_value=(None, "8.8.8.8")), \
             patch("hub_dialogue_events._post", side_effect=fake_post):
            sub = self.call("events/subscribe", self.params)["result"]
            self.assertTrue(sub["id"].startswith("sub_"))
            again = self.call("events/subscribe", self.params)["result"]
            self.assertEqual(sub["id"], again["id"])
            mid = dialogue.queue_message("operations", "Please inspect failing build", source="hub")
            sent = dialogue.deliver_pending(channel="operations")
            self.assertEqual(sent["delivered"], [mid])
            self.assertEqual(dialogue.deliver_pending()["delivered"], [])
            evt = [c[1] for c in calls if c[1].get("name") == dialogue.EVENT_NAME][0]
            self.assertEqual(evt["data"]["text"], "Please inspect failing build")
            self.assertEqual(evt["data"]["message_id"], mid)
            response = self.call("tools/call", {"name": "hub_dialogue_reply", "arguments": {
                "message_id": mid, "reply": "CI passed on isolated runner", "status": "completed",
                "evidence": "job: CI-123, commit: abc"}})
            self.assertFalse(response["result"]["isError"])
            self.assertEqual(dialogue.read_message(mid)["status"], "completed")
            self.assertEqual(self.call("events/unsubscribe", self.params)["result"], {})
            self.assertEqual(dialogue.deliver_pending()["delivered"], [])
            with dialogue.database() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0], 0)

    def test_challenge_failure_blocks_subscription(self):
        dialogue.set_enabled(True, confirmed=True)
        with patch("hub_dialogue_events._destination", return_value=(None, "8.8.8.8")), \
             patch("hub_dialogue_events._post", return_value=(200, b'{"challenge":"wrong"}')):
            result = self.call("events/subscribe", self.params)
        self.assertEqual(result["error"]["code"], -32015)
        with dialogue.database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0], 0)

    def test_completed_reply_requires_evidence(self):
        dialogue.set_enabled(True, confirmed=True)
        mid = dialogue.queue_message("operations", "Test")
        with dialogue.database() as conn:
            conn.execute("UPDATE messages SET status='delivered' WHERE id=?", (mid,))
        with self.assertRaises(dialogue.DialogueError):
            dialogue.reply_message(mid, "Done", "completed", "")
        self.assertEqual(dialogue.reply_message(mid, "Blocked", "blocked", "")["status"], "blocked")

    def test_idempotent_automatic_incident(self):
        dialogue.set_enabled(True, confirmed=True)
        a = dialogue.queue_message("operations", "Failed job", source="hub",
                                   idempotency_key="job-123:step-3:failed")
        b = dialogue.queue_message("operations", "Repeated failure", source="hub",
                                   idempotency_key="job-123:step-3:failed")
        self.assertEqual(a, b)
        with dialogue.database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)

    def test_reply_requires_verified_delivery(self):
        dialogue.set_enabled(True, confirmed=True)
        mid = dialogue.queue_message("operations", "Test")
        with self.assertRaises(dialogue.DialogueError):
            dialogue.reply_message(mid, "Done", "completed", "")
        with self.assertRaises(dialogue.DialogueError):
            dialogue.queue_message("Operations", "No")

    def test_subscriptions_persist_across_connections(self):
        dialogue.set_enabled(True, confirmed=True)
        with patch("hub_dialogue_events._destination", return_value=(None, "8.8.8.8")), \
             patch("hub_dialogue_events._post", side_effect=lambda u, s, i, e, p:
                   (200, json.dumps({"challenge": p["challenge"]}).encode())):
            self.call("events/subscribe", self.params)
        with dialogue.database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
