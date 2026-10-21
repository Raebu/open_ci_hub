"""Signed private GitHub transport: exact ZIP, replay and untrusted issue rejection."""
import base64
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
import pc_releases as pr
import signed_preview_queue as q
import github_signed_preview_delivery as d


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for p in (patch.object(pr, "ROOT", self.root / "releases"),
                  patch.object(pr, "_owner"),
                  patch.object(q, "MIN_FREE_DISK", 0)):
            p.start()
            self.addCleanup(p.stop)
        pr.enrol("demo", service=True, health_port=17171, confirm=True)
        self.key = Ed25519PrivateKey.generate()
        pem = self.root / "pub.pem"
        pem.write_bytes(self.key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo))
        q.pin("demo", repository="Raebu/Service_Business",
              issuer="https://signer.example.test",
              public_key_path=pem, ref_prefixes=["refs/heads/feature/"],
              required_checks=["test"], confirm=True)
        q.enable_preview("demo", confirm=True)
        self.sha = "a" * 40
        inside = BytesIO()
        with ZipFile(inside, "w") as f:
            f.writestr("dist/index.html", "<h1>test</h1>")
        self.inner = inside.getvalue()
        outer = BytesIO()
        with ZipFile(outer, "w") as f:
            f.writestr("release.zip", self.inner)
        self.outer = outer.getvalue()
        stamp = int(time.time())
        payload = {
            "schema": q.SCHEMA, "issuer": "https://signer.example.test",
            "app": "demo", "repository": "Raebu/Service_Business",
            "ref": "refs/heads/feature/test", "commit": self.sha,
            "checked_out_sha": self.sha,
            "artifact_sha256": hashlib.sha256(self.inner).hexdigest(),
            "run_id": "123", "run_status": "passed",
            "checks": {"test": {"exit_code": 0, "observed": True,
                                "timed_out": False, "log_sha256": "c" * 64}},
            "issued_at": stamp, "expires_at": stamp + 600,
            "nonce": base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("="),
            "environment": "preview",
        }
        self.signed = {
            "payload": payload, "signature": base64.urlsafe_b64encode(
                self.key.sign(q.canonical(payload))).decode().rstrip("=")}
        self.repo = {"id": 1427302798, "private": True,
                     "full_name": "Raebu/open-ci-chatgpt-control"}
        self.issue = {"number": 101, "state": "open",
                      "title": "[open-ci-preview] demo",
                      "user": {"login": "Raebu"}, "body": json.dumps(self.signed)}
        self.run = {"event": "push", "status": "completed", "conclusion": "success",
                    "head_sha": self.sha, "head_branch": "feature/test",
                    "head_repository": {"full_name": "Raebu/Service_Business"}}
        self.artifacts = {"total_count": 1, "artifacts": [{
            "id": 33, "name": "release-bundle", "expired": False,
            "digest": "sha256:" + hashlib.sha256(self.outer).hexdigest()}]}
        d.enrol("demo", control_repo="Raebu/open-ci-chatgpt-control",
                trusted_issue_authors=["Raebu"], artifact_name="release-bundle",
                confirm=True, api=self.api)

    def api(self, tool, endpoint):
        self.assertEqual(tool, "gh")
        if endpoint == "repos/Raebu/open-ci-chatgpt-control":
            return self.repo
        if endpoint.endswith("/issues?state=open&per_page=100"):
            return [self.issue]
        if endpoint.endswith("/artifacts?per_page=100"):
            return self.artifacts
        if endpoint.endswith("/runs/123"):
            return self.run
        raise AssertionError(endpoint)

    def blob(self, tool, endpoint, *, size_limit):
        self.assertTrue(endpoint.endswith("/artifacts/33/zip"))
        return self.outer

    def poll(self):
        return d.poll_once("demo", api=self.api, binary=self.blob)

    def test_queued_once_from_valid_issue(self):
        self.assertEqual(self.poll()["count"], 1)
        self.assertEqual(q.status("demo")["queue"]["queued"], 1)
        self.assertEqual(self.poll()["count"], 0)
        self.assertIsNone(pr.status("demo")["current_commit"])

    def test_bad_signature_and_author_cannot_queue(self):
        self.issue["body"] = json.dumps({"payload": self.signed["payload"],
                                         "signature": "FAKE"})
        self.assertEqual(self.poll()["count"], 0)
        self.issue["body"] = json.dumps(self.signed)
        self.issue["user"]["login"] = "other"
        self.assertEqual(self.poll()["count"], 0)

    def test_wrong_sha_or_download_digest_rejected(self):
        self.run["head_sha"] = "f" * 40
        self.assertEqual(self.poll()["count"], 0)
        self.run["head_sha"] = self.sha
        self.artifacts["artifacts"][0]["digest"] = "sha256:" + "f" * 64
        self.assertEqual(self.poll()["count"], 0)

    def test_repo_privacy_and_replay_consent(self):
        self.repo["private"] = False
        with self.assertRaises(PermissionError):
            self.poll()
        self.repo["private"] = True
        owner = q._policy("demo")
        owner["preview_auto"] = False
        from hub import secure_write
        secure_write(q._policy_path("demo"), json.dumps(owner))
        with self.assertRaises(PermissionError):
            self.poll()


if __name__ == "__main__":
    unittest.main()
