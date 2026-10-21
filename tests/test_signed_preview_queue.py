"""Real Ed25519 signing tests for the opt-in local preview release queue."""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

import pc_releases as pr
import signed_preview_queue as queue


class SignedPreviewQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "pc-releases"
        for x in (patch.object(pr, "ROOT", self.root),
                  patch.object(pr, "_owner"),
                  patch.object(pr, "_restart"),
                  patch.object(pr, "_healthy", return_value=True)):
            x.start()
            self.addCleanup(x.stop)
        pr.enrol("demo", service=True, health_port=17171, confirm=True)
        self.key = Ed25519PrivateKey.generate()
        public = self.key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo)
        self.pem = self.base / "trusted-issuer.pem"
        self.pem.write_bytes(public)
        queue.pin("demo", repository="Raebu/Service_Business",
                  issuer="https://example.test/independent-ci-attestor",
                  public_key_path=self.pem,
                  ref_prefixes=["refs/heads/feature/"],
                  required_checks=["test", "build"], confirm=True)
        self.sha = "a" * 40
        self.archive = self.base / "release.zip"
        with ZipFile(self.archive, "w") as z:
            z.writestr("dist/index.html", "<h1>Preview</h1>")
        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.manifest = self.base / "request.json"
        self.enable()

    def enable(self):
        queue.enable_preview("demo", confirm=True)

    def make_receipt(self, **changes):
        now = int(time.time())
        payload = {
            "schema": queue.SCHEMA, "issuer": "https://example.test/independent-ci-attestor",
            "app": "demo", "repository": "Raebu/Service_Business",
            "ref": "refs/heads/feature/login", "commit": self.sha,
            "checked_out_sha": self.sha, "artifact_sha256": self.digest,
            "run_id": "trusted-build-876", "run_status": "passed",
            "environment": "preview", "issued_at": now,
            "expires_at": now + 3600,
            "nonce": base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("="),
            "checks": {
                "test": {"exit_code": 0, "observed": True,
                         "timed_out": False, "log_sha256": "c" * 64},
                "build": {"exit_code": 0, "observed": True,
                          "timed_out": False, "log_sha256": "d" * 64},
            },
        }
        payload.update(changes)
        signature = self.key.sign(queue.canonical(payload))
        return {"payload": payload, "signature":
                base64.urlsafe_b64encode(signature).decode().rstrip("=")}

    def write_manifest(self, message):
        self.manifest.write_text(json.dumps(message))
        return self.manifest

    def test_signed_request_is_queued_and_promoted_with_receipt(self):
        receipt = self.make_receipt()
        stored = queue.submit("demo", self.write_manifest(receipt), self.archive)
        self.assertEqual(stored["status"], "queued")
        self.assertEqual(queue.status("demo")["queue"]["queued"], 1)
        result = queue.run_once("demo")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(pr.status("demo")["current_commit"], self.sha)
        self.assertEqual(result["outcome"]["archive_sha256"], self.digest)
        self.assertEqual(queue.run_once("demo")["status"], "idle")
        self.assertEqual(queue.status("demo")["queue"]["succeeded"], 1)
        # The nonce is still journalled, and never replayed after consuming ZIP.
        with self.assertRaises(PermissionError):
            queue.submit("demo", self.manifest, self.archive)

    def test_forgery_and_payload_tampering_never_enter_queue(self):
        signed = self.make_receipt()
        for prop, value in [
            ("commit", "b" * 40), ("checked_out_sha", "b" * 40),
            ("artifact_sha256", "e" * 64), ("run_status", "skipped"),
            ("repository", "Raebu/Other"), ("environment", "production"),
            ("ref", "refs/heads/main"), ("ref", "refs/heads/feature/../main"),
            ("issuer", "https://evil.test"),
            ("checks", {"build": signed["payload"]["checks"]["build"]}),
        ]:
            with self.subTest(prop=prop, value=str(value)[:20]):
                broken = json.loads(json.dumps(signed))
                broken["payload"][prop] = value
                with self.assertRaises(PermissionError):
                    queue.submit("demo", self.write_manifest(broken), self.archive)
        self.assertEqual(queue.status("demo")["queue"]["queued"], 0)

    def test_invalid_signature_wrong_key_and_expiry(self):
        raw = self.make_receipt()
        raw["signature"] = base64.urlsafe_b64encode(
            Ed25519PrivateKey.generate().sign(queue.canonical(raw["payload"]))
        ).decode().rstrip("=")
        with self.assertRaises(PermissionError):
            queue.submit("demo", self.write_manifest(raw), self.archive)
        old = int(time.time()) - 1000
        expired = self.make_receipt(issued_at=old, expires_at=old + 10)
        with self.assertRaises(PermissionError):
            queue.submit("demo", self.write_manifest(expired), self.archive)

    def test_artifact_tampering_is_rejected_without_queueing(self):
        receipt = self.make_receipt()
        with ZipFile(self.archive, "a") as z:
            z.writestr("dist/evil.html", "not signed")
        with self.assertRaises(PermissionError):
            queue.submit("demo", self.write_manifest(receipt), self.archive)
        self.assertEqual(queue.status("demo")["queue"]["queued"], 0)

    def test_failed_health_check_restores_prior_release_and_blocks_retry(self):
        first = self.make_receipt()
        queue.submit("demo", self.write_manifest(first), self.archive)
        self.assertEqual(queue.run_once("demo")["status"], "succeeded")
        archive2 = self.base / "release2.zip"
        with ZipFile(archive2, "w") as z:
            z.writestr("dist/index.html", "broken")
        sha2 = "b" * 40
        digest2 = hashlib.sha256(archive2.read_bytes()).hexdigest()
        second = self.make_receipt(commit=sha2, checked_out_sha=sha2,
                                   artifact_sha256=digest2)
        queue.submit("demo", self.write_manifest(second), archive2)
        with patch.object(pr, "_healthy", side_effect=[False, True]):
            result = queue.run_once("demo")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(pr.status("demo")["current_commit"], self.sha)
        self.assertEqual(queue.status("demo")["queue"]["failed"], 1)
        self.assertEqual(queue.run_once("demo")["status"], "idle")

    def test_no_standing_consent_and_no_first_time_nonservice_preview(self):
        data = queue._policy("demo")
        data["preview_auto"] = False
        from hub import secure_write
        secure_write(queue._policy_path("demo"), json.dumps(data))
        with self.assertRaises(PermissionError):
            queue.submit("demo", self.write_manifest(self.make_receipt()), self.archive)
        with self.assertRaises(PermissionError):
            queue.run_once("demo")
        with self.assertRaises(PermissionError):
            queue.enable_preview("demo", confirm=False)

    def test_revoking_local_preview_consent_stops_an_existing_queue(self):
        receipt = self.make_receipt()
        queue.submit("demo", self.write_manifest(receipt), self.archive)
        data = queue._policy("demo")
        data["preview_auto"] = False
        from hub import secure_write
        secure_write(queue._policy_path("demo"), json.dumps(data))
        with self.assertRaises(PermissionError):
            queue.run_once("demo")
        self.assertIsNone(pr.status("demo")["current_commit"])
        self.assertEqual(queue.status("demo")["queue"]["queued"], 1)

    def test_cannot_sign_away_failed_checks_or_old_receipt(self):
        signed_failed = self.make_receipt(checks={
            "test": {"exit_code": 1, "observed": True, "timed_out": False,
                     "log_sha256": "c" * 64},
            "build": {"exit_code": 0, "observed": True, "timed_out": False,
                      "log_sha256": "d" * 64}
        })
        signed_failed["signature"] = base64.urlsafe_b64encode(
            self.key.sign(queue.canonical(signed_failed["payload"]))
        ).decode().rstrip("=")
        with self.assertRaises(PermissionError):
            queue.submit("demo", self.write_manifest(signed_failed), self.archive)
        now = int(time.time())
        stale = self.make_receipt(issued_at=now - 90000, expires_at=now + 1000)
        stale["signature"] = base64.urlsafe_b64encode(
            self.key.sign(queue.canonical(stale["payload"]))
        ).decode().rstrip("=")
        with self.assertRaises(PermissionError):
            queue.submit("demo", self.write_manifest(stale), self.archive)


if __name__ == "__main__":
    unittest.main()
