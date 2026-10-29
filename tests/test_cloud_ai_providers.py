"""No-network tests of optional cloud AI setup and billing guardrails."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import cloud_ai_providers as ai

ROOT = Path(__file__).resolve().parents[1]


class AIProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.key_dir = Path(self.temp.name) / ".config" / "open-ci-hub" / "cloud-ai-keys"
        patcher = patch.object(ai, "KEY_DIR", self.key_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_four_configured_providers_with_fixed_https_origins(self):
        self.assertEqual(set(ai.PROVIDERS), {"gemini", "nvidia", "openrouter", "groq"})
        for item in ai.catalog():
            self.assertTrue(ai.PROVIDERS[item["id"]].endpoint.startswith("https://"))
            self.assertEqual(item["credential"], "not_configured")
            self.assertFalse(item["automatic_requests"])
            self.assertFalse(item["auto_billing"])
        self.assertFalse(self.key_dir.exists())

    def test_owner_key_setup_requires_local_tty(self):
        with patch.object(ai.sys.stdin, "isatty", return_value=False):
            with self.assertRaises(PermissionError):
                ai.add_key("gemini")
        self.assertFalse(self.key_dir.exists())

    def test_private_key_is_hidden_and_permissions_are_enforced(self):
        with patch.object(ai.sys.stdin, "isatty", return_value=True), \
             patch.object(ai.getpass, "getpass", return_value="TEST_ONLY_ABC123_TESTABC456"), \
             redirect_stdout(StringIO()) as out:
            ai.add_key("groq")
        self.assertNotIn("TEST_ONLY_ABC", out.getvalue())
        self.assertEqual(stat.S_IMODE(self.key_dir.stat().st_mode), 0o700)
        p = self.key_dir / "groq.key"
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertTrue(ai.has_key("groq"))
        self.assertEqual(ai._read_key("groq"), "TEST_ONLY_ABC123_TESTABC456")
        p.chmod(0o644)
        with self.assertRaises(PermissionError):
            ai._read_key("groq")

    def test_unsafe_symlinked_key_is_refused(self):
        self.key_dir.mkdir(parents=True, mode=0o700)
        target = Path(self.temp.name) / "outside"
        target.write_text("TEST_ONLY_SECRET_LONG")
        (self.key_dir / "gemini.key").symlink_to(target)
        with self.assertRaises(PermissionError):
            ai.has_key("gemini")

    def test_missing_free_account_confirmation_never_calls_network(self):
        with patch.object(ai, "_read_key", side_effect=AssertionError("should not read key")), \
             patch.object(ai, "build_opener", side_effect=AssertionError("should not call network")):
            for provider in ai.PROVIDERS:
                with self.assertRaises(PermissionError):
                    ai.test_one(provider, confirm_free=False)

    def test_manual_call_is_single_explicit_capped_request(self):
        response = {"choices":[{"message":{"content":"READY"}}]}
        class Resp:
            status=200
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def read(self,n): return json.dumps(response).encode()
        class Client:
            def __init__(self): self.calls=[]
            def open(self, req, timeout):
                self.calls.append((req,timeout))
                return Resp()
        client=Client()
        with patch.object(ai, "_read_key", return_value="TEST_ONLY_ABC123_TESTABC456"), \
             patch.object(ai, "build_opener", return_value=client):
            result = ai.test_one("openrouter", confirm_free=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["response"], "READY")
        self.assertEqual(len(client.calls), 1)
        req,timeout=client.calls[0]
        self.assertEqual(req.full_url, ai.PROVIDERS["openrouter"].endpoint)
        self.assertEqual(req.get_method(), "POST")
        self.assertLessEqual(timeout,20)
        payload=json.loads(req.data)
        self.assertEqual(payload["model"],"openrouter/free")
        self.assertEqual(payload["max_tokens"],32)
        self.assertEqual(len(payload["messages"]),1)

    def test_activation_requires_verified_nonempty_response_and_can_be_disabled(self):
        self.key_dir.mkdir(parents=True, mode=0o700)
        p=self.key_dir / "gemini.key"
        p.write_text("TEST_ONLY_ABC123_TESTABC456")
        p.chmod(0o600)
        with self.assertRaises(RuntimeError):
            ai.enable_tested("gemini", {"provider":"gemini", "status":"empty_reply",
                                       "response":"", "retry_performed":False})
        self.assertFalse(ai.catalog()[0]["activated"])
        ai.enable_tested("gemini", {"provider":"gemini", "status":"completed",
                                   "response":"READY","retry_performed":False})
        self.assertTrue(ai.catalog()[0]["activated"])
        self.assertEqual(stat.S_IMODE(ai._activation_file().stat().st_mode), 0o600)
        self.assertNotIn("TEST_ONLY_ABC123", ai._activation_file().read_text())
        ai.disable("gemini")
        self.assertFalse(ai.catalog()[0]["activated"])

    def test_activation_does_not_allow_credentialless_provider(self):
        ai.enable_tested("nvidia", {"provider":"nvidia", "status":"completed",
                                   "response":"READY","retry_performed":False})
        item=next(x for x in ai.catalog() if x["id"]=="nvidia")
        self.assertFalse(item["activated"])

    def test_dashboard_has_official_manual_links(self):
        page=(ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        for p in ai.PROVIDERS.values():
            self.assertIn(p.key_page, page)
        self.assertIn("python3 cloud_ai_providers.py status",page)
        self.assertNotIn("type=\"password\"", page[page.index('id="ai-models"'):page.index('id="whatsapp-compose"')])


if __name__ == "__main__":
    unittest.main()
