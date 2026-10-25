"""Provider API evidence must reject false-green and wrong artifact bytes."""
from __future__ import annotations
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from zipfile import ZipFile

import provider_artifact_verifier as v


def wrapper():
    inner = BytesIO()
    with ZipFile(inner, "w") as f:
        f.writestr("dist/index.html", "<h1>hello</h1>")
    outer = BytesIO()
    with ZipFile(outer, "w") as f:
        f.writestr("release.zip", inner.getvalue())
    return outer.getvalue(), inner.getvalue()


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.sha = "a" * 40
        self.repo = "Raebu/example"
        self.ref = "refs/heads/feature/example"
        self.wrapper, self.release = wrapper()
        self.run = {
            "event": "push", "status": "completed", "conclusion": "success",
            "head_sha": self.sha, "head_branch": "feature/example",
            "head_repository": {"full_name": self.repo},
            "actor": {"login": "Raebu"}, "workflow_id": 123,
        }
        self.jobs = {"jobs": [
            {"id": 55, "name": "test", "status": "completed",
             "conclusion": "success", "runner_name": "open-ci-hub-garuda",
             "labels": ["self-hosted", "linux", "local-ci"],
             "steps": [
                 {"name": "checkout", "status": "completed", "conclusion": "success"},
                 {"name": "test", "status": "completed", "conclusion": "success"}]},
            {"id": 56, "name": "build", "status": "completed",
             "conclusion": "success", "runner_name": "open-ci-hub-garuda",
             "labels": ["self-hosted", "linux", "local-ci"],
             "steps": [{"name": "build", "status": "completed", "conclusion": "success"}]}
        ], "total_count": 2}
        self.artifacts = {
            "artifacts": [{"id": 66, "name": "release-bundle",
                           "expired": False, "digest":
                           "sha256:" + hashlib.sha256(self.wrapper).hexdigest()}],
            "total_count": 1}
        self.glproject = {"path_with_namespace": self.repo, "id": 888}
        self.pipeline = {"id": 777, "sha": self.sha, "ref": "feature/example",
                         "status": "success", "source": "push",
                         "user": {"username": "Raebu"}}
        self.gljobs = [
            {"name": "test", "status": "success", "id": 55,
             "runner": {"id": 1}, "tag_list": ["local-ci-isolated"]},
            {"name": "build", "status": "success", "id": 56,
             "runner": {"id": 1}, "tag_list": ["local-ci-isolated"]},
        ]

    def api(self, tool, endpoint):
        if tool == "gh":
            if endpoint.endswith("/jobs?per_page=100"):
                return self.jobs
            if endpoint.endswith("/artifacts?per_page=100"):
                return self.artifacts
            return self.run
        if endpoint.startswith("projects/") and "/pipelines/" not in endpoint:
            return self.glproject
        if endpoint.endswith("/jobs?per_page=100&include_retried=true"):
            return self.gljobs
        return self.pipeline

    def binary(self, tool, endpoint, *, size_limit):
        if endpoint.endswith("/zip") or endpoint.endswith("/artifacts"):
            return self.wrapper
        return b"real returned CI logs\nexit 0\n"

    def gh(self):
        return v.verify_github(self.repo, "555", self.sha, self.ref, "Raebu",
                               ["test", "build"], workflow_id=123,
                               api=self.api, binary=self.binary)

    def gl(self):
        return v.verify_gitlab(self.repo, "777", self.sha, self.ref, "Raebu",
                               ["test", "build"], artifact_job="build",
                               api=self.api, binary=self.binary)

    def test_github_validates_provider_job_logs_and_downloaded_zip(self):
        data, inner = self.gh()
        self.assertEqual(inner, self.release)
        self.assertFalse(data["independently_attested"])
        self.assertEqual(data["artifact_sha256"], hashlib.sha256(inner).hexdigest())
        self.assertEqual(set(data["checks"]), {"test", "build"})

    def test_gitlab_validates_pipeline_jobs_and_actual_zip(self):
        data, inner = self.gl()
        self.assertEqual(inner, self.release)
        self.assertEqual(data["provider"], "gitlab")
        self.assertEqual(data["run_status"], "passed")

    def test_github_false_green_and_untrusted_runner_denied(self):
        for field, value in (
            ("head_sha", "b" * 40), ("conclusion", "failure"),
            ("status", "queued"), ("workflow_id", 999),
            ("event", "pull_request"), ("head_repository", {"full_name": "fork/evil"}),
            ("head_branch", "main"), ("actor", {"login": "intruder"})
        ):
            original = self.run[field]
            try:
                self.run[field] = value
                with self.subTest(field=field), self.assertRaises(PermissionError):
                    self.gh()
            finally:
                self.run[field] = original
        job = self.jobs["jobs"][0]
        for field, value in (
            ("conclusion", "skipped"), ("runner_name", "github-hosted-5"),
            ("labels", ["ubuntu-latest"]), ("steps", [])
        ):
            original = job[field]
            try:
                job[field] = value
                with self.subTest(field=field), self.assertRaises(PermissionError):
                    self.gh()
            finally:
                job[field] = original

    def test_gitlab_failed_or_renamed_job_denied(self):
        for field, value in (("sha", "b" * 40), ("status", "failed"),
                             ("source", "merge_request_event"),
                             ("ref", "main"), ("user", {"username": "wrong"})):
            original = self.pipeline[field]
            try:
                self.pipeline[field] = value
                with self.subTest(field=field), self.assertRaises(PermissionError):
                    self.gl()
            finally:
                self.pipeline[field] = original
        original = self.gljobs[1]["tag_list"]
        self.gljobs[1]["tag_list"] = ["shared-runner"]
        with self.assertRaises(PermissionError):
            self.gl()
        self.gljobs[1]["tag_list"] = original

    def test_wrong_artifact_checksum_content_and_duplicates_denied(self):
        self.artifacts["artifacts"][0]["digest"] = "sha256:" + "b" * 64
        with self.assertRaises(PermissionError):
            self.gh()
        self.artifacts["artifacts"][0]["digest"] = ("sha256:" +
                                                  hashlib.sha256(self.wrapper).hexdigest())
        self.artifacts["artifacts"].append(dict(self.artifacts["artifacts"][0]))
        with self.assertRaises(PermissionError):
            self.gh()
        self.artifacts["artifacts"].pop()
        with self.assertRaises(PermissionError):
            v._bundle(b"not-a-zip")
        outer = BytesIO()
        with ZipFile(outer, "w") as f:
            f.writestr("secret.txt", "no")
        with self.assertRaises(PermissionError):
            v._bundle(outer.getvalue())

    def test_reject_incomplete_pagination_and_missing_log(self):
        self.jobs["total_count"] = 101
        with self.assertRaises(PermissionError):
            self.gh()
        self.jobs["total_count"] = 2
        def no_log(tool, endpoint, *, size_limit):
            if endpoint.endswith("/logs"):
                return b""
            return self.binary(tool, endpoint, size_limit=size_limit)
        with self.assertRaises(PermissionError):
            v.verify_github(self.repo, "555", self.sha, self.ref, "Raebu",
                            ["test", "build"], workflow_id=123, api=self.api,
                            binary=no_log)

    def test_untrusted_claim_cannot_sign_or_submit_to_pc(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            folder.chmod(0o700)
            data, inner = self.gh()
            files = v.write_unsigned_evidence(str(folder), data, inner)
            self.assertFalse(files["deployable"])
            self.assertEqual(Path(files["release_zip"]).read_bytes(), self.release)
            self.assertNotIn("signature", json.loads(
                Path(files["unsigned_candidate"]).read_text()))
            with self.assertRaises(PermissionError):
                v.write_unsigned_evidence(str(folder), data, inner)
            folder.chmod(0o755)
            with self.assertRaises(PermissionError):
                v.write_unsigned_evidence(str(folder), data, inner)


if __name__ == "__main__":
    unittest.main()
