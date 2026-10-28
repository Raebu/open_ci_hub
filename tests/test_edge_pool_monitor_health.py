"""End-to-end monitor quality and offline-worker acceptance, no real phones."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app_runtime import Broker
from edge_pool import EdgePool
import edge_pool_monitor as monitor


class EdgeMonitorHealthTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base=Path(tmp.name)
        self.repo=self.base/"open-ci-hub"
        self.repo.mkdir()
        self.manifest=self.base/"manifest.json"
        self.script=self.repo/"check.py"
        self.script.write_text("x=1\n",encoding="utf8")
        self.manifest.write_text(json.dumps(["check.py"]))
        self.broker=Broker(self.base/"broker")
        for app in ("s22-edge","s25-edge"):
            self.broker.register(app,["python.syntax","json.inspect"],confirm=True)
            self.broker.set_task_budget(app,"python.syntax",128,confirm=True)
            self.broker.set_task_budget(app,"json.inspect",128,confirm=True)
            self.broker.set_parallel(app,1,confirm=True)
            token=self.broker.issue(app,"worker",confirm=True)["token"]
            self.broker.enroll_edge(app,"android_arm64",confirm=True)
            if app=="s25-edge":
                self.broker.edge_availability(
                    self.broker.auth(token),
                    {"platform":"Android","architecture":"arm64",
                     "free_ram_mb":3600,"charging":True})
        self.pool=EdgePool(self.broker)
        self.patches=(patch.object(monitor,"PROJECTS_ROOT",self.base),
                      patch.object(monitor,"ROOT",self.repo))
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_invalid_python_fails_locally_without_executing_on_phone(self):
        self.script.write_text("def x(:\n")
        result=monitor.run(self.manifest,pool=self.pool)
        self.assertEqual(len(result["local_checks"]),1)
        self.assertFalse(result["local_checks"][0]["valid"])
        self.assertFalse(result["dispatched"])
        health=monitor.build_status(result,self.pool)
        self.assertEqual(health["state"],"failed")
        self.assertEqual(health["local_checks"]["failed"],1)
        with self.broker._db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM jobs").fetchone()[0],0)

    def test_offline_both_still_checks_locally_and_defers_remote(self):
        with self.broker._db() as db:
            db.execute("UPDATE fleet_devices SET last_seen=0")
        result=monitor.run(self.manifest,pool=self.pool)
        self.assertTrue(result["local_checks"][0]["valid"])
        self.assertFalse(result["dispatched"])
        self.assertEqual(len(result["deferred"]),1)
        health=monitor.build_status(result,self.pool)
        self.assertEqual(health["state"],"degraded")
        self.assertEqual(health["remote_checks"]["deferred"],1)
        self.assertTrue(any(i["kind"]=="no_eligible_mobile_workers"
                            for i in health["issues"]))

    def test_available_s25_offline_s22_degraded_with_evidence(self):
        result=monitor.run(self.manifest,pool=self.pool)
        self.assertEqual(len(result["dispatched"]),1)
        self.assertEqual(result["dispatched"][0]["device"],"s25-edge")
        summary=monitor.build_status(result,self.pool)
        self.assertEqual(summary["state"],"degraded")
        self.assertTrue(any(i["kind"]=="worker_offline_or_unavailable"
                            and i["device"]=="s22-edge" for i in summary["issues"]))

    def test_verified_invalid_receipt_is_not_quality_pass(self):
        sent=self.pool.submit("python.syntax",{"source":"def bad(:\n"},
                              request_key="invalid-checked-2026",confirm=True)
        worker=self.broker.auth(
            # Find previously issued identity through a fresh *test-only*
            # role token; we do not use this extra token for enrolment.
            self.broker.issue("s25-edge","worker",confirm=True)["token"])
        # This extra token is not the enrolment token; claim cannot be
        # successful for an approved enrolled worker using it.
        self.assertIsNone(self.broker.claim(worker)["job"])
        # Simulate the acknowledged fixed worker response with the same
        # immutable result, not fabricated user input.
        from portable_edge_worker import task
        with self.broker._db() as db:
            db.execute("UPDATE jobs SET status='completed',attempts=1,result=? WHERE id=?",
                       (json.dumps(task("python.syntax",{"source":"def bad(:\n"})),sent["job_id"]))
        got=self.pool.status(sent["pool_job"])
        self.assertTrue(got["verified"])
        self.assertFalse(got["check_passed"])
        self.assertEqual(self.pool.report()["totals"]["checks_failed"],1)

    def test_status_atomic_private_and_does_not_contain_source(self):
        report={"state":"degraded","local_checks":{"passed":1},
                "issues":[{"kind":"worker_offline_or_unavailable","device":"s22-edge"}]}
        location=self.base/"private"/"status.json"
        monitor.save_status(report,location)
        self.assertEqual(json.loads(location.read_text()),report)
        self.assertEqual(location.stat().st_mode & 0o777,0o600)
        location.unlink()
        location.symlink_to(self.script)
        with self.assertRaises(ValueError):
            monitor.save_status(report,location)
        self.assertEqual(self.script.read_text(),"x=1\n")


if __name__=="__main__":
    unittest.main()
