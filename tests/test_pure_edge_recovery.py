"""Bounded recovery for idempotent Android tasks. No arbitrary lease replay."""
import tempfile
from pathlib import Path
import unittest

from app_runtime import Broker

class PureEdgeRecoveryTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.b=Broker(Path(folder.name)/"test-state")
        self.b.register("s25-test",["json.inspect","python.syntax","hash.benchmark"],confirm=True)
        self.b.issue("s25-test","worker",confirm=True)
        self.b.enroll_edge("s25-test","android_arm64",confirm=True)
        self.submit=self.b.auth(self.b.issue("s25-test","submit",confirm=True)["token"])

    def stranded(self,*,attempts=1,key="pure-check-0001",task="json.inspect",lease=100):
        identifier=self.b.submit(self.submit,task,{"text":"{}"},
                                 idempotency_key=key)["job_id"]
        with self.b._db() as db:
            db.execute('UPDATE jobs SET status=?,attempts=?,lease_until=?,updated=? WHERE id=?',
                       ('stranded',attempts,lease,lease,identifier))
        return identifier

    def status(self,identifier):
        with self.b._db() as db:
            return dict(db.execute('SELECT status,attempts,lease_until,lease_hash FROM jobs WHERE id=?',
                                   (identifier,)).fetchone())

    def test_android_pure_job_requeues_once_only_after_grace(self):
        identifier=self.stranded()
        self.assertFalse(self.b.reconcile_pure_edge_stranded(now=1000)["requeued_pure_edge_jobs"])
        self.assertEqual(self.status(identifier)["status"],"stranded")
        result=self.b.reconcile_pure_edge_stranded(now=1001)
        self.assertEqual(result["requeued_pure_edge_jobs"],[identifier])
        self.assertFalse(result["arbitrary_code_replayed"])
        self.assertEqual(self.status(identifier)["status"],"queued")
        self.assertIsNone(self.status(identifier)["lease_until"])
        self.assertEqual(self.status(identifier)["attempts"],1)
        self.assertFalse(self.b.reconcile_pure_edge_stranded(now=9999)["requeued_pure_edge_jobs"])

    def test_exhausted_pure_job_is_finalised_not_replayed(self):
        identifier=self.stranded(attempts=3)
        result=self.b.reconcile_pure_edge_stranded(now=1200)
        self.assertEqual(result["finalised_pure_edge_jobs"],[identifier])
        self.assertEqual(result["requeued_pure_edge_jobs"],[])
        self.assertEqual(self.status(identifier)["status"],"failed")

    def test_without_idempotency_key_is_never_replayed(self):
        identifier=self.stranded(key="pure-check-0002")
        with self.b._db() as db:
            db.execute('UPDATE jobs SET idempotency_key=NULL WHERE id=?',(identifier,))
        result=self.b.reconcile_pure_edge_stranded(now=1200)
        self.assertEqual(result["requeued_pure_edge_jobs"],[])
        self.assertEqual(self.status(identifier)["status"],"stranded")

    def test_other_device_kind_not_replayed(self):
        with self.b._db() as db:
            db.execute('UPDATE fleet_devices SET kind=? WHERE app=?',
                       ("windows_arm64","s25-test"))
        identifier=self.stranded()
        result=self.b.reconcile_pure_edge_stranded(now=1200)
        self.assertEqual(result["requeued_pure_edge_jobs"],[])
        self.assertEqual(self.status(identifier)["status"],"stranded")

    def test_non_stranded_or_missing_expiry_not_replayed(self):
        identifier=self.stranded()
        with self.b._db() as db:
            db.execute('UPDATE jobs SET lease_until=NULL WHERE id=?',(identifier,))
        self.assertFalse(self.b.reconcile_pure_edge_stranded(now=9999)["requeued_pure_edge_jobs"])
        self.assertEqual(self.status(identifier)["status"],"stranded")
