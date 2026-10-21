"""Regression tests for Open CI Hub's local-first CI execution bridge."""
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import ci_execution_bridge as bridge


def write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(0o600)


def repo_entry(*, protected=False, allow_prs=False, allow_network=False):
    return {
        "provider": "github", "repo": "Raebu/open_ci_hub", "branches": ["main"],
        "allow_pull_requests": allow_prs, "allow_network": allow_network,
        "require_protected_branch": protected, "allowed_images": ["python:3.12-slim"],
    }


class BridgeTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.local = self.root / ".local"
        self.local.mkdir(mode=0o700)
        self.config_path = self.local / "ci-bridge-config.json"
        self.secrets_path = self.local / "ci-bridge-secrets.json"
        self.db_path = self.local / "ci-execution.sqlite3"
        config = bridge.default_config()
        config["repos"] = [repo_entry()]
        write_private(self.config_path, config)
        write_private(self.secrets_path, {
            "schema": bridge.SECRETS_SCHEMA,
            "owner_token": "owner-" + "x" * 48,
            "github_webhook_secret": "github-secret-" + "g" * 48,
            "gitlab_webhook_secret": "gitlab-secret-" + "l" * 48,
        })
        self.patches = [
            patch.object(bridge, "LOCAL", self.local),
            patch.object(bridge, "CONFIG_PATH", self.config_path),
            patch.object(bridge, "SECRETS_PATH", self.secrets_path),
            patch.object(bridge, "DB_PATH", self.db_path),
        ]
        for item in self.patches:
            item.start()
        self.queue = bridge.Queue(self.db_path)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def sha(self, letter="a"):
        return letter * 40

    def push_payload(self, sha=None):
        return {"repository":{"full_name":"Raebu/open_ci_hub"},
                "ref":"refs/heads/main","after":sha or self.sha(),
                "sender":{"login":"Raebu"}}

    def workflow_raw(self, deployment=False):
        text = '''
version = 1
image = "python:3.12-slim"
network = "none"
memory_mb = 1024
cpus = 1.0
timeout_seconds = 300
artifacts = ["dist"]

[[steps]]
name = "unit tests"
stage = "test"
run = "python -m unittest discover -s tests -v"
timeout_seconds = 300
'''
        if deployment:
            text += '''
[deployment]
application = "serviceapp"
bundle = "dist/release.tar.gz"
signature = "dist/release.tar.gz.sig"
signer = "reviewer"
'''
        return text


class LocalStateTests(BridgeTestCase):
    def test_init_requires_local_confirmation_and_never_overwrites(self):
        local = self.root / "fresh"
        with patch.object(bridge, "LOCAL", local), \
             patch.object(bridge, "CONFIG_PATH", local / "ci-bridge-config.json"), \
             patch.object(bridge, "SECRETS_PATH", local / "ci-bridge-secrets.json"), \
             patch.object(bridge, "require_owner"):
            with self.assertRaises(PermissionError):
                bridge.init_local(confirm=False)
            result = bridge.init_local(confirm=True)
            self.assertTrue(result["initialised"])
            self.assertGreaterEqual(len(result["owner_token"]), 40)
            self.assertEqual((local / "ci-bridge-secrets.json").stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                bridge.init_local(confirm=True)

    def test_config_rejects_invalid_concurrency(self):
        config = bridge.read_config()
        config["max_concurrent"] = 20
        write_private(self.config_path, config)
        with self.assertRaises(RuntimeError):
            bridge.read_config()

    def test_old_queue_schema_is_migrated_without_dropping_jobs(self):
        legacy = self.root / "legacy.sqlite3"
        with sqlite3.connect(legacy) as db:
            db.execute("""CREATE TABLE jobs (
                id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                provider TEXT NOT NULL, repo TEXT NOT NULL, sha TEXT NOT NULL,
                state TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 50,
                created_at INTEGER NOT NULL)""")
            db.execute("INSERT INTO jobs(id,idempotency_key,provider,repo,sha,state,created_at) VALUES(?,?,?,?,?,?,?)",
                       ("existing","key","github","Raebu/open_ci_hub","a"*40,"failed",1))
        legacy.chmod(0o600)
        migrated = bridge.Queue(legacy)
        with migrated._db() as db:
            columns={row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            row=db.execute("SELECT id,state,actor,image_id,deployment_app FROM jobs WHERE id='existing'").fetchone()
        self.assertTrue({"actor","image_id","deployment_app","not_before"}.issubset(columns))
        self.assertEqual((row["id"],row["state"],row["actor"],row["image_id"],row["deployment_app"]),
                         ("existing","failed","","",""))
        self.assertEqual(bridge.default_config()["min_free_ram_mb"],3072)

    def test_queue_deduplicates_webhook_and_poller_for_same_sha(self):
        first, created = self.queue.enqueue(
            provider="github", repo="Raebu/open_ci_hub", event="push", action="push",
            ref="main", sha=self.sha(), source_repo="Raebu/open_ci_hub", actor="Raebu")
        second, created_again = self.queue.enqueue(
            provider="github", repo="Raebu/open_ci_hub", event="poll", action="branch-head",
            ref="main", sha=self.sha(), source_repo="Raebu/open_ci_hub", actor="poller")
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.queue.jobs()), 1)
        self.assertEqual(self.queue.jobs()[0]["actor"], "Raebu")

    def test_queue_rejects_non_allowlisted_branch_and_bad_sha(self):
        with self.assertRaises(PermissionError):
            self.queue.enqueue(provider="github", repo="Raebu/open_ci_hub", event="push", action="push",
                               ref="feature/unsafe", sha=self.sha(), source_repo="Raebu/open_ci_hub")
        with self.assertRaises(ValueError):
            self.queue.enqueue(provider="github", repo="Raebu/open_ci_hub", event="push", action="push",
                               ref="main", sha="not-a-sha", source_repo="Raebu/open_ci_hub")

    def test_queue_claim_limits_concurrency_and_retry_is_bounded(self):
        first, _ = self.queue.enqueue(provider="github", repo="Raebu/open_ci_hub", event="push", action="push",
                                      ref="main", sha=self.sha("a"), source_repo="Raebu/open_ci_hub")
        self.queue.enqueue(provider="github", repo="Raebu/open_ci_hub", event="push", action="push",
                           ref="main", sha=self.sha("b"), source_repo="Raebu/open_ci_hub")
        claimed = self.queue.claim(1)
        self.assertEqual(claimed["id"], first["id"])
        self.assertIsNone(self.queue.claim(1))
        self.queue.finish(first["id"], "failed", error="expected test failure")
        with patch("ci_execution_bridge.require_owner"):
            retried = self.queue.retry(first["id"], confirm=True)
        self.assertEqual(retried["state"], "pending")
        self.assertEqual(retried["step_index"], 0)

    def test_resource_deferred_job_keeps_its_attempt_and_waits_for_cooldown(self):
        job, _ = self.queue.enqueue(provider="github", repo="Raebu/open_ci_hub", event="push", action="push",
                                    ref="main", sha=self.sha(), source_repo="Raebu/open_ci_hub")
        self.queue.claim(1)
        deferred = self.queue.defer(job["id"], "low available memory")
        self.assertEqual(deferred["state"], "pending")
        self.assertEqual(deferred["attempts"], 0)
        self.assertGreater(deferred["not_before"], bridge.now())
        self.assertIn("Deferred", deferred["summary"])
        self.assertIsNone(self.queue.claim(1))

    def test_approval_requires_an_attached_offline_signature(self):
        job, _ = self.queue.enqueue(provider="github", repo="Raebu/open_ci_hub", event="push", action="push",
                                    ref="main", sha=self.sha(), source_repo="Raebu/open_ci_hub")
        workflow = bridge.validate_workflow(self.workflow_raw(deployment=True), repo_entry())
        workspace = self.root / "workspace"
        dist = workspace / "dist"
        dist.mkdir(parents=True)
        (dist / "release.tar.gz").write_bytes(b"test release bundle")
        log = self.root / "job.log"
        self.queue.update_workflow(job["id"], workflow, self.sha(), workspace, log)
        self.queue.finish(job["id"], "awaiting_approval", summary="Awaiting signed release")
        with patch("ci_execution_bridge.require_owner"):
            with self.assertRaises(PermissionError):
                self.queue.approve(job["id"], confirm=True)
            signature_source = self.root / "release.tar.gz.sig"
            signature_source.write_bytes(b"signature")
            attached = bridge.attach_signature(job["id"], str(signature_source), confirm=True)
            self.assertTrue(attached["attached"])
            approved = self.queue.approve(job["id"], confirm=True)
        self.assertEqual(approved["state"], "pending")
        self.assertEqual(approved["deployment_approved"], 1)

    def test_cancel_marks_running_job_without_claiming_completion(self):
        job, _ = self.queue.enqueue(provider="github", repo="Raebu/open_ci_hub", event="push", action="push",
                                    ref="main", sha=self.sha(), source_repo="Raebu/open_ci_hub")
        self.queue.claim(1)
        with patch("ci_execution_bridge.require_owner"):
            result = self.queue.cancel(job["id"], confirm=True)
        self.assertEqual(result["state"], "running")
        self.assertTrue(self.queue.is_cancelled(job["id"]))

    def test_cancel_wins_race_with_resource_deferral(self):
        job, _ = self.queue.enqueue(provider="github", repo="Raebu/open_ci_hub", event="push", action="push",
                                    ref="main", sha=self.sha(), source_repo="Raebu/open_ci_hub")
        self.queue.claim(1)
        with patch("ci_execution_bridge.require_owner"):
            self.queue.cancel(job["id"], confirm=True)
        result = self.queue.defer(job["id"], "resource pressure")
        self.assertEqual(result["state"], "cancelled")
        self.assertIsNotNone(result["finished_at"])

    def test_provider_status_failures_are_retained(self):
        job = {"id":"a"*32,"provider":"github","state":"failed"}
        with patch("ci_execution_bridge._provider_status_impl", side_effect=RuntimeError("provider auth unavailable")):
            with self.assertRaises(RuntimeError):
                bridge._provider_status(job,"failed","failed")
        with self.queue._db() as db:
            row=db.execute("SELECT outcome,detail FROM status_reports WHERE job_id=?",(job["id"],)).fetchone()
        self.assertEqual(row["outcome"],"error")
        self.assertIn("provider auth unavailable",row["detail"])


class WebhookTests(BridgeTestCase):
    def start_server(self):
        server = bridge.BridgeServer(("127.0.0.1", 0), bridge.make_handler(self.queue), self.queue)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def post_github(self, base, payload, delivery="delivery-123", signature_secret=None):
        body = json.dumps(payload, separators=(",", ":")).encode()
        secret = signature_secret or "github-secret-" + "g" * 48
        signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        request = Request(base + "/webhooks/github", data=body, method="POST", headers={
            "Content-Type":"application/json","X-GitHub-Event":"push",
            "X-GitHub-Delivery":delivery,"X-Hub-Signature-256":signature})
        return urlopen(request, timeout=5)

    def test_github_hmac_webhook_enqueues_and_replay_is_idempotent(self):
        base = self.start_server()
        with self.post_github(base, self.push_payload()) as response:
            first = json.loads(response.read())
            self.assertEqual(response.status, 202)
            self.assertTrue(first["accepted"])
            self.assertTrue(first["created"])
        with self.post_github(base, self.push_payload()) as response:
            duplicate = json.loads(response.read())
            self.assertTrue(duplicate["duplicate"])
        self.assertEqual(len(self.queue.jobs()), 1)

    def test_invalid_signature_is_rejected(self):
        base = self.start_server()
        with self.assertRaises(HTTPError) as caught:
            self.post_github(base, self.push_payload(), signature_secret="wrong-" + "z" * 48)
        self.assertEqual(caught.exception.code, 401)
        self.assertEqual(len(self.queue.jobs()), 0)

    def test_protected_branch_policy_fails_closed(self):
        config = bridge.read_config()
        config["repos"][0]["require_protected_branch"] = True
        write_private(self.config_path, config)
        base = self.start_server()
        with patch("ci_execution_bridge._protected_branch", return_value=False):
            with self.assertRaises(HTTPError) as caught:
                self.post_github(base, self.push_payload())
        self.assertEqual(caught.exception.code, 403)
        self.assertEqual(len(self.queue.jobs()), 0)

    def test_fork_pull_request_is_rejected(self):
        payload = {"repository":{"full_name":"Raebu/open_ci_hub"},"action":"opened",
                   "pull_request":{"head":{"ref":"patch-1","sha":self.sha(),"repo":{"full_name":"unknown/fork"}},
                                   "base":{"ref":"main","sha":self.sha("b")}}}
        result, reason = bridge._normalise_event("github", "pull_request", payload)
        self.assertIsNone(result)
        self.assertIn("fork", reason)


class WorkflowSafetyTests(BridgeTestCase):
    def test_valid_workflow_is_normalised_and_deployment_is_signed_only(self):
        workflow = bridge.validate_workflow(self.workflow_raw(), repo_entry())
        self.assertEqual(workflow["image"], "python:3.12-slim")
        self.assertEqual(workflow["steps"][0]["stage"], "test")
        self.assertEqual(workflow["artifacts"], ["dist"])
        deployment = bridge.validate_workflow(self.workflow_raw(deployment=True), repo_entry())
        self.assertEqual(deployment["deployment"]["application"], "serviceapp")
        with self.assertRaises(ValueError):
            bridge.validate_workflow(self.workflow_raw().replace('stage = "test"', 'stage = "deploy"'), repo_entry())

    def test_workflow_rejects_unapproved_image_network_and_path_traversal(self):
        with self.assertRaises(PermissionError):
            bridge.validate_workflow(self.workflow_raw().replace("python:3.12-slim", "unknown:latest"), repo_entry())
        with self.assertRaises(PermissionError):
            bridge.validate_workflow(self.workflow_raw().replace('network = "none"', 'network = "bridge"'), repo_entry())
        with self.assertRaises(ValueError):
            bridge.validate_workflow(self.workflow_raw().replace('artifacts = ["dist"]', 'artifacts = ["../.ssh"]'), repo_entry())
        with self.assertRaises(ValueError):
            bridge.validate_workflow(self.workflow_raw(deployment=True).replace('bundle = "dist/release.tar.gz"', 'bundle = "../outside.tar.gz"'), repo_entry())

    def test_container_has_limits_and_no_host_docker_socket(self):
        workflow = bridge.validate_workflow(self.workflow_raw(), repo_entry())
        argv = bridge._container_argv({"id":"a"*32}, workflow, "sha256:"+"a"*64,
                                      workflow["steps"][0], self.root/"workspace")
        joined = " ".join(argv)
        self.assertIn("--network none", joined)
        self.assertIn("--read-only", joined)
        self.assertIn("--memory 1024m", joined)
        self.assertIn("--pids-limit 128", joined)
        self.assertNotIn("--privileged", joined)
        self.assertNotIn("/var/run/docker.sock", joined)
        self.assertIn("sha256:"+"a"*64, argv)

    def test_pull_request_jobs_do_not_reuse_shared_cache(self):
        job={"event":"pull_request","provider":"github","repo":"Raebu/open_ci_hub"}
        with patch.object(bridge,"CACHE_ROOT",self.root/"cache"):
            self.assertIsNone(bridge._cache_dir(job,"sha256:"+"a"*64))
        push={**job,"event":"push"}
        with patch.object(bridge,"CACHE_ROOT",self.root/"cache"):
            cache=bridge._cache_dir(push,"sha256:"+"a"*64)
            self.assertTrue(cache.is_dir())
            self.assertEqual(cache.stat().st_mode&0o777,0o700)

    def test_bounded_command_timeout_does_not_block_on_silent_output(self):
        result=bridge._run_bounded([sys.executable,"-c","import time; time.sleep(2)"],timeout=1)
        self.assertTrue(result["timed_out"])
        self.assertNotEqual(result["exit_code"],0)

    def test_callback_failure_does_not_leave_an_active_process(self):
        def fail():
            raise RuntimeError("simulated queue read failure")
        with self.assertRaisesRegex(RuntimeError,"simulated queue read failure"):
            bridge._run_bounded([sys.executable,"-c","import time; time.sleep(10)"],
                                timeout=15,job_id="b"*32,should_cancel=fail)
        self.assertNotIn("b"*32,bridge.ACTIVE_PROCESSES)


if __name__ == "__main__":
    unittest.main()
