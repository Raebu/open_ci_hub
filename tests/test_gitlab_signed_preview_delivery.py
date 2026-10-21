"""Private GitLab signed artifacts: real Ed25519, job logs and reject paths."""
from __future__ import annotations
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
import gitlab_signed_preview_delivery as delivery
import pc_releases as releases
import signed_preview_queue as queue


class GitLabDeliveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for p in (patch.object(releases, "ROOT", self.root / "apps"),
                  patch.object(releases, "_owner"),
                  patch.object(queue, "MIN_FREE_DISK", 0)):
            p.start()
            self.addCleanup(p.stop)
        releases.enrol("demo", service=True, health_port=17171, confirm=True)
        self.key = Ed25519PrivateKey.generate()
        pub = self.root / "signer.pem"
        pub.write_bytes(self.key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo))
        queue.pin("demo", repository="Theraeburngroup/service",
                  issuer="https://trusted-attestor.example.test",
                  public_key_path=pub, ref_prefixes=["refs/heads/feature/"],
                  required_checks=["test"], confirm=True)
        queue.enable_preview("demo", confirm=True)
        self.sha = "b" * 40
        inner = BytesIO()
        with ZipFile(inner, "w") as z:
            z.writestr("dist/index.html", "GitLab preview test")
        self.inner = inner.getvalue()
        outer = BytesIO()
        with ZipFile(outer, "w") as z:
            z.writestr("release.zip", self.inner)
        self.outer = outer.getvalue()
        self.trace = b"Actual GitLab CI runner completed test 0\n"
        trace_sha = hashlib.sha256(self.trace).hexdigest()
        now = int(time.time())
        payload = {
            "schema": queue.SCHEMA, "issuer": "https://trusted-attestor.example.test",
            "app": "demo", "repository": "Theraeburngroup/service",
            "ref": "refs/heads/feature/hello", "commit": self.sha,
            "checked_out_sha": self.sha, "run_id": "777",
            "run_status": "passed", "environment": "preview",
            "artifact_sha256": hashlib.sha256(self.inner).hexdigest(),
            "checks": {"test": {"observed": True, "exit_code": 0,
                                "timed_out": False, "log_sha256": trace_sha}},
            "issued_at": now, "expires_at": now + 600,
            "nonce": base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("="),
        }
        self.signed = {"payload": payload, "signature": base64.urlsafe_b64encode(
            self.key.sign(queue.canonical(payload))).decode().rstrip("=")}
        self.private = {"id": 321, "path_with_namespace": "Theraeburngroup/control",
                        "visibility": "private"}
        self.source = {"id": 654, "path_with_namespace": "Theraeburngroup/service"}
        self.pipeline = {"id": 777, "sha": self.sha,
                         "ref": "feature/hello", "status": "success",
                         "source": "push", "user": {"username": "Raebu"}}
        self.jobs = [{"id": 888, "name": "test", "status": "success",
                      "runner": {"id": 9}, "tag_list": ["local-ci-isolated"]},
                     {"id": 889, "name": "build", "status": "success",
                      "runner": {"id": 9}, "tag_list": ["local-ci-isolated"]}]
        self.issues = [{"iid": 17, "state": "opened",
                        "title": "[open-ci-preview] demo",
                        "author": {"username": "Raebu"},
                        "description": json.dumps(self.signed)}]
        delivery.enrol("demo", control_project="Theraeburngroup/control",
                       trusted_authors=["Raebu"], source_actor="Raebu",
                       artifact_job="build", confirm=True, api=self.api)

    def api(self, tool, endpoint):
        self.assertEqual(tool, "glab")
        if endpoint == "projects/Theraeburngroup%2Fcontrol" or endpoint == "projects/321":
            return self.private
        if endpoint == "projects/Theraeburngroup%2Fservice":
            return self.source
        if endpoint == "projects/654/pipelines/777":
            return self.pipeline
        if endpoint.endswith("/jobs?per_page=100&include_retried=true"):
            return self.jobs
        if endpoint == "projects/321/issues?state=opened&per_page=100":
            return self.issues
        raise AssertionError("Unexpected GitLab API "+ endpoint)

    def binary(self, tool, endpoint, *, size_limit):
        self.assertEqual(tool, "glab")
        if endpoint == "projects/654/jobs/888/trace":
            return self.trace
        if endpoint == "projects/654/jobs/889/artifacts":
            return self.outer
        raise AssertionError("Unexpected GitLab binary API "+endpoint)

    def poll(self):
        return delivery.poll_once("demo", api=self.api, binary=self.binary)

    def test_signed_verified_gitlab_job_and_artifact_queued_only_once(self):
        result = self.poll()
        self.assertEqual(result["count"], 1)
        self.assertEqual(queue.status("demo")["queue"]["queued"], 1)
        self.assertEqual(self.poll()["count"], 0)
        self.assertIsNone(releases.status("demo")["current_commit"])

    def test_gitlab_failed_or_skipped_pipeline_denied(self):
        for status in ("failed", "canceled", "skipped", "manual", "running"):
            with self.subTest(status=status):
                self.pipeline["status"] = status
                self.assertEqual(self.poll()["count"], 0)
        self.pipeline["status"] = "success"
        self.jobs[0]["status"] = "failed"
        self.assertEqual(self.poll()["count"], 0)
        self.jobs[0]["status"] = "success"

    def test_gitlab_log_digest_and_artifact_mutation_denied(self):
        self.trace = b"Changed provider job log"
        self.assertEqual(self.poll()["count"], 0)
        self.trace = b"Actual GitLab CI runner completed test 0\n"
        self.outer = self.outer + b"changed bytes"
        self.assertEqual(self.poll()["count"], 0)

    def test_private_control_scope_and_signature_required(self):
        self.private["visibility"] = "public"
        with self.assertRaises(PermissionError):
            self.poll()
        self.private["visibility"] = "private"
        self.issues[0]["description"] = '{"payload": {}, "signature": "bogus"}'
        self.assertEqual(self.poll()["count"], 0)
        self.issues[0]["description"] = json.dumps(self.signed)
        self.private["id"] = 999
        with self.assertRaises(PermissionError):
            self.poll()

    def test_issue_author_is_transport_filter_and_consent_is_revocable(self):
        self.issues[0]["author"]["username"] = "notRaebu"
        self.assertEqual(self.poll()["count"], 0)
        self.issues[0]["author"]["username"] = "Raebu"
        owner = queue._policy("demo")
        owner["preview_auto"] = False
        from hub import secure_write
        secure_write(queue._policy_path("demo"), json.dumps(owner))
        with self.assertRaises(PermissionError):
            self.poll()


if __name__ == "__main__":
    unittest.main()
