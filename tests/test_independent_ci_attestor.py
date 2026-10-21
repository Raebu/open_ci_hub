"""Independent attestor: key never in CI, exact provider verification and real signature."""
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

import independent_ci_attestor as a
import signed_preview_queue as q


class IndependentAttestorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.root.chmod(0o700)
        self.private = self.root / "secret"
        self.private.mkdir(mode=0o700)
        self.delivery = self.root / "handoff"
        self.delivery.mkdir(mode=0o700)
        self.key = Ed25519PrivateKey.generate()
        self.pem = self.private / "attestor.pem"
        self.pem.write_bytes(self.key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()))
        self.pem.chmod(0o600)
        self.policyfile = self.private / "policy.json"
        self.settings = {
            "schema": a.POLICY_SCHEMA,
            "issuer": "https://attestor.example.test",
            "app": "preview-service",
            "repository": "Raebu/Service_Business", "provider": "github",
            "trusted_actor": "Raebu", "ref_prefixes": ["refs/heads/feature/"],
            "required_checks": ["test", "build"], "workflow_id": 123,
            "artifact_job": None, "private_key_path": str(self.pem),
            "signer_uid": os.geteuid(),
            "blocked_runner_uids": [999999],
        }
        self._write_policy()
        self.sha = "a" * 40
        self.artifact = b"TEST RELEASE ZIP CONTENT IS COVERED BY PROVIDER HASH"
        self.record = {
            "schema": "open-ci-hub.provider-evidence.v1",
            "source": "read-only-provider-API",
            "independently_attested": False, "provider": "github",
            "repository": "Raebu/Service_Business", "run_id": "123456",
            "commit": self.sha, "checked_out_sha": self.sha,
            "ref": "refs/heads/feature/test", "actor": "Raebu",
            "run_status": "passed",
            "artifact_sha256": hashlib.sha256(self.artifact).hexdigest(),
            "checks": {
                "test": {"exit_code": 0, "observed": True, "timed_out": False,
                         "log_sha256": "a" * 64},
                "build": {"exit_code": 0, "observed": True, "timed_out": False,
                          "log_sha256": "b" * 64},
            },
        }

    def _write_policy(self):
        self.policyfile.write_text(json.dumps(self.settings))
        self.policyfile.chmod(0o600)

    def _verify(self, repo, run_id, sha, ref, actor, checks, *, workflow_id):
        self.assertEqual((repo, run_id, sha, ref, actor, checks, workflow_id),
                         ("Raebu/Service_Business", "123456", self.sha,
                          "refs/heads/feature/test", "Raebu", ["test", "build"], 123))
        return self.record, self.artifact

    def _sign(self):
        settings = a.policy(self.policyfile)
        return a.verify_and_sign(settings, run_id="123456", sha=self.sha,
                                 ref="refs/heads/feature/test",
                                 github_verify=self._verify)

    def test_separate_signer_generates_valid_queue_manifest(self):
        signed, data = self._sign()
        self.assertEqual(data, self.artifact)
        self.assertEqual(signed["payload"]["environment"], "preview")
        self.assertEqual(signed["payload"]["artifact_sha256"],
                         hashlib.sha256(self.artifact).hexdigest())
        trust = {
            "issuer": self.settings["issuer"],
            "repository": self.settings["repository"],
            "ref_prefixes": self.settings["ref_prefixes"],
            "required_checks": self.settings["required_checks"],
            "public_key_pem": self.key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
        }
        with patch.object(q, "_policy", return_value=trust):
            accepted = q.verify_signed("preview-service", signed)
        self.assertEqual(accepted["commit"], self.sha)
        written = a.handoff(str(self.delivery), signed, data)
        self.assertEqual(written["status"], "signed_waiting_for_authenticated_delivery")
        self.assertEqual(Path(written["release_zip"]).read_bytes(), data)
        with self.assertRaises(PermissionError):
            a.handoff(str(self.delivery), signed, data)

    def test_unsigned_spoofed_candidate_or_checksum_mismatch_rejected(self):
        self.record["artifact_sha256"] = "f" * 64
        with self.assertRaises(PermissionError):
            self._sign()

    def test_mutated_signature_and_production_scope_rejected_by_pc_verifier(self):
        signed, _ = self._sign()
        pub = self.key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        trust = {"issuer": self.settings["issuer"], "repository": self.settings["repository"],
                 "ref_prefixes": self.settings["ref_prefixes"],
                 "required_checks": self.settings["required_checks"],
                 "public_key_pem": pub}
        with patch.object(q, "_policy", return_value=trust):
            signed["payload"]["environment"] = "production"
            with self.assertRaises(PermissionError):
                q.verify_signed("preview-service", signed)

    def test_signer_refuses_ci_process_and_wrong_uid(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            with self.assertRaises(PermissionError):
                a.policy(self.policyfile)
        self.settings["signer_uid"] = 1234567
        self._write_policy()
        with self.assertRaises(PermissionError):
            a.policy(self.policyfile)

    def test_key_permissions_and_runner_uid_separation_enforced(self):
        self.pem.chmod(0o644)
        with self.assertRaises(PermissionError):
            self._sign()
        self.pem.chmod(0o600)
        self.settings["blocked_runner_uids"] = [os.geteuid()]
        self._write_policy()
        with self.assertRaises(PermissionError):
            a.policy(self.policyfile)

    def test_only_enrolled_branch_prefix_may_sign(self):
        c = a.policy(self.policyfile)
        with self.assertRaises(PermissionError):
            a.verify_and_sign(c, run_id="123456", sha=self.sha,
                              ref="refs/heads/main", github_verify=self._verify)

    def test_refuses_non_private_handoff_directory(self):
        signed, data = self._sign()
        self.delivery.chmod(0o755)
        with self.assertRaises(PermissionError):
            a.handoff(str(self.delivery), signed, data)


if __name__ == "__main__":
    unittest.main()
