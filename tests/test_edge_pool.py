"""Shared phone pool: idempotency, capability safety, fairness, evidence and failover."""
import json
from pathlib import Path
import tempfile
import time
import unittest

from app_runtime import Broker
from edge_pool import EdgePool, validate_payload, digest
from edge_pool_cli import _payload_from_file
from resource_guard import ResourceGate


class EdgePoolTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name) / "state"
        self.b = Broker(self.state)
        self.devices = {}
        for app in ("s22-edge","s25-edge"):
            self.b.register(app,["json.inspect","python.syntax","hash.benchmark"],confirm=True)
            for task in ("json.inspect","python.syntax","hash.benchmark"):
                self.b.set_task_budget(app,task,128,0,confirm=True)
            self.b.set_parallel(app,1,confirm=True)
            credential = self.b.issue(app,"worker",confirm=True)["token"]
            self.devices[app] = self.b.auth(credential)
            self.b.enroll_edge(app,"android_arm64",confirm=True)
            self.b.edge_availability(self.devices[app], {
                "platform":"Android","architecture":"arm64",
                "free_ram_mb":3072,"charging":True})
        self.now = time.time()
        self.pool = EdgePool(self.b, clock=lambda:self.now)

    def submit(self, key="audit-test-0001", payload=None, task="json.inspect"):
        return self.pool.submit(task,payload or {"text":'{"a":1}'},
                                request_key=key,confirm=True)

    def test_schedules_across_two_phones_and_uses_allowlisted_work(self):
        first=self.submit("fairness-0001")
        second=self.submit("fairness-0002")
        self.assertNotEqual(first["device"],second["device"])
        self.assertFalse(first["duplicate"])
        self.assertEqual(self.pool.status(first["pool_job"])["status"],"queued")
        with self.assertRaises(ValueError):
            self.pool.submit("shell",{"command":"rm -rf /"},request_key="unsafe-0001",confirm=True)
        with self.assertRaises(PermissionError):
            self.pool.submit("json.inspect",{"text":"{}"},
                             request_key="unconfirmed-1")
        self.assertTrue(self.pool.audit()["valid"])
        self.assertEqual(self.pool.audit()["checked_events"],2)

    def test_exact_idempotency_and_different_payload_is_rejected(self):
        first=self.submit("same-request-001")
        second=self.submit("same-request-001")
        self.assertEqual(first["pool_job"],second["pool_job"])
        self.assertTrue(second["duplicate"])
        with self.assertRaises(RuntimeError):
            self.pool.submit("json.inspect",{"text":"{}"},
                             request_key="same-request-001",confirm=True)
        with self.b._db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],1)

    def test_invalid_and_sensitive_rejected(self):
        for task,payload in [
            ("json.inspect",{"text":"{}","run":"sh"}),
            ("python.syntax",{"source":"x"*1501}),
            ("hash.benchmark",{"text":"hi","iterations":True}),
            ("hash.benchmark",{"text":"hi","iterations":60000})
        ]:
            with self.assertRaises(ValueError):
                validate_payload(task,payload)
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/"credentials.py"
            file.write_text('api_key = "example-value"\n')
            with self.assertRaises(ValueError):
                _payload_from_file(str(file))
            file.unlink()
            file.symlink_to("/etc/passwd")
            with self.assertRaises(ValueError):
                _payload_from_file(str(file))

    def test_completed_receipt_verified_and_tamper_detected(self):
        sent=self.submit("verified-job-001")
        target=self.devices[sent["device"]]
        lease=self.b.claim(target)["job"]
        self.assertEqual(lease["job_id"],sent["job_id"])
        self.b.complete(target,lease["job_id"],lease["lease_token"],
                        True,{"valid":True,"kind":"dict",
                              "sha256":digest({"a":1})})
        got=self.pool.status(sent["pool_job"])
        self.assertEqual(got["status"],"completed")
        self.assertTrue(got["verified"])
        self.assertEqual(got["attempts"],1)
        self.assertTrue(self.pool.audit()["valid"])
        # Completed state can be forged, but won't verify against independent expected results.
        with self.b._db() as db:
            db.execute("UPDATE jobs SET result=? WHERE id=?",
                       (json.dumps({"valid":True,"kind":"dict","sha256":"0"*64}),sent["job_id"]))
        self.assertFalse(self.pool.status(sent["pool_job"])["verified"])
        # Hash chain itself catches edits to its recorded history.
        with self.b._db() as db:
            db.execute("UPDATE edge_pool_events SET detail='tampered' WHERE pool_job=?",
                       (sent["pool_job"],))
        self.assertFalse(self.pool.audit()["valid"])

    def test_stale_worker_not_selected(self):
        with self.b._db() as db:
            db.execute("UPDATE fleet_devices SET last_seen=? WHERE app='s22-edge'",
                       (self.now-500,))
        dispatched=self.submit("fresh-worker-001")
        self.assertEqual(dispatched["device"],"s25-edge")
        with self.b._db() as db:
            db.execute("UPDATE fleet_devices SET charging=0 WHERE app='s25-edge'")
        with self.assertRaises(RuntimeError):
            self.submit("no-capacity-001")

    def test_unclaimed_offline_failover_is_atomic_and_never_duplicates_execution(self):
        sent=self.submit("failover-0001")
        initial_device=sent["device"]
        new_device=({"s22-edge","s25-edge"}-{initial_device}).pop()
        with self.b._db() as db:
            db.execute("UPDATE fleet_devices SET last_seen=? WHERE app=?",
                       (self.now-500,initial_device))
        self.now+=60
        out=self.pool.reconcile()
        self.assertEqual(out["reassigned"][0]["to"],new_device)
        final=self.pool.status(sent["pool_job"])
        self.assertEqual(final["device"],new_device)
        self.assertEqual(final["moves"],1)
        self.assertEqual(self.b.claim(self.devices[initial_device])["job"],None)
        new_claim=self.b.claim(self.devices[new_device])["job"]
        self.assertEqual(new_claim["job_id"],final["device_job"])
        self.b.complete(self.devices[new_device],new_claim["job_id"],
                        new_claim["lease_token"],True,
                        {"valid":True,"kind":"dict","sha256":digest({"a":1})})
        self.assertTrue(self.pool.status(sent["pool_job"])["verified"])
        with self.b._db() as db:
            self.assertEqual(db.execute(
                "SELECT status FROM jobs WHERE id=?", (sent["job_id"],)
            ).fetchone()[0],"failed")
        self.assertTrue(self.pool.audit()["valid"])

    def test_claimed_work_cannot_move_to_other_phone(self):
        sent=self.submit("leased-wont-move-001")
        lease=self.b.claim(self.devices[sent["device"]])["job"]
        with self.b._db() as db:
            db.execute("UPDATE fleet_devices SET last_seen=? WHERE app=?",
                       (self.now-500,sent["device"]))
        self.now+=60
        self.assertFalse(self.pool.reconcile()["reassigned"])
        self.assertEqual(self.pool.status(sent["pool_job"])["device"],sent["device"])
        self.assertEqual(self.pool.status(sent["pool_job"])["status"],"leased")

    def test_policy_pause_blocks_new_dispatch(self):
        with self.b._db() as db:
            ResourceGate.set_controls(db,paused=True)
        with self.assertRaises(RuntimeError):
            self.submit("pause-blocked-001")

    def test_failed_receipt_not_incorrectly_successful(self):
        sent=self.submit("worker-failed-001")
        with self.b._db() as db:
            db.execute("UPDATE jobs SET status='failed',attempts=3,result=? WHERE id=?",
                       (json.dumps({"error":"device_failure"}),sent["job_id"]))
        state=self.pool.status(sent["pool_job"])
        self.assertFalse(state["verified"])
        self.assertEqual(state["status"],"failed")


if __name__=="__main__":
    unittest.main()
