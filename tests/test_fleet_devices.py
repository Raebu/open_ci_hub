"""Owner-approved remote device dispatch, isolation and zero-cloud-billing guard."""
from pathlib import Path
from resource_guard import ResourceGate
import hashlib
import tempfile
import unittest
import app_runtime as runtime

class FleetBrokerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.b=runtime.Broker(Path(self.tmp.name)/"state")
        self.b.resource_gate=ResourceGate(memory_reader=lambda:1000,gpu_reader=lambda:{})
        self.b.register("samsung-edge",["python.syntax","json.inspect"],confirm=True)
        self.b.set_task_budget("samsung-edge","json.inspect",128,confirm=True)
        self.b.set_task_budget("samsung-edge","python.syntax",128,confirm=True)
        self.worker=self.b.auth(self.b.issue("samsung-edge","worker",confirm=True)["token"])
        self.submitter=self.b.auth(self.b.issue("samsung-edge","submit",confirm=True)["token"])
        self.job=self.b.submit(self.submitter,"json.inspect",{"text":"{}"})["job_id"]

    def test_enrolment_requires_owner_and_one_worker_key(self):
        with self.assertRaises(PermissionError):
            self.b.enroll_edge("samsung-edge","windows_arm64")
        with self.assertRaises(ValueError):
            self.b.enroll_edge("samsung-edge","cloud_arm64",confirm=True)
        outcome=self.b.enroll_edge("samsung-edge","windows_arm64",confirm=True)
        self.assertEqual(outcome["kind"],"windows_arm64")
        with self.assertRaises(Exception):
            self.b.enroll_edge("samsung-edge","windows_arm64",confirm=True)

    def test_edge_rebind_rotates_worker_and_invalidates_old_token(self):
        b=runtime.Broker(Path(self.tmp.name)/"rebind-state")
        tasks=["python.syntax","json.inspect","hash.benchmark"]
        b.register("s25-edge",tasks,confirm=True)
        old_token=b.issue("s25-edge","worker",confirm=True)["token"]
        b.enroll_edge("s25-edge","android_arm64",confirm=True)
        new_token="oci_v1_"+"N"*64
        new_hash=hashlib.sha256(new_token.encode()).hexdigest()
        result=b.rebind_edge("s25-edge",new_hash,confirm=True)
        self.assertTrue(result["rebound"])
        self.assertTrue(result["previous_worker_revoked"])
        with self.assertRaises(PermissionError):
            b.auth(old_token)
        self.assertEqual(b.auth(new_token)["credential_hash"],new_hash)
        device=b.edge_devices()[0]
        self.assertFalse(device["online"])
        self.assertIsNone(device["last_seen"])

    def test_edge_rebind_requires_confirmation_and_blocks_active_jobs(self):
        with self.assertRaises(PermissionError):
            self.b.rebind_edge("samsung-edge","a"*64)
        with self.assertRaises(ValueError):
            self.b.rebind_edge("samsung-edge","not-a-hash",confirm=True)
        self.b.enroll_edge("samsung-edge","windows_arm64",confirm=True)
        with self.assertRaises(RuntimeError):
            self.b.rebind_edge("samsung-edge","b"*64,confirm=True)
        self.assertEqual(self.b.edge_devices()[0]["approved"],True)

    def test_unenrolled_broker_uses_host_task_budget_without_edge_bypass(self):
        # Unenrolled apps still pass through host admission and their declared task budget.
        out=self.b.claim(self.worker)
        self.assertIsNotNone(out["job"])
        self.assertEqual(out["job"]["resources"]["ram_mb"],128)

    def test_enrolled_edge_capacity_and_exact_task(self):
        self.b.enroll_edge("samsung-edge","windows_arm64",confirm=True)
        initial=self.b.claim(self.worker)
        self.assertIsNone(initial["job"])
        self.assertEqual(initial["reason"],"device_telemetry_stale")
        with self.assertRaises(ValueError):
            self.b.edge_availability(self.worker,{"platform":"Linux","architecture":"x86_64",
                                        "free_ram_mb":2200,"charging":True})
        with self.assertRaises(PermissionError):
            self.b.edge_availability(self.submitter,{"platform":"Windows","architecture":"arm64",
                                          "free_ram_mb":2200,"charging":True})
        report=self.b.edge_availability(self.worker,{"platform":"Windows","architecture":"arm64",
                                          "free_ram_mb":2000,"charging":True})
        self.assertTrue(report["online"])
        self.assertTrue(self.b.edge_devices()[0]["online"])
        # Even with only 1GB available on Garuda, a separately bounded remote
        # device may take an approved tiny job, without using the host runner.
        claimed=self.b.claim(self.worker)
        self.assertEqual(claimed["job"]["job_id"],self.job)
        self.assertEqual(claimed["job"]["resources"]["ram_mb"],128)
        receipt=self.b.complete(self.worker,self.job,claimed["job"]["lease_token"],
                                True,{"valid":True})
        self.assertEqual(receipt["status"],"completed")

    def test_no_ac_telemetry_expiration_and_single_worker_identity(self):
        self.b.enroll_edge("samsung-edge","windows_arm64",confirm=True)
        self.b.edge_availability(self.worker,{"platform":"Windows","architecture":"arm64",
                                         "free_ram_mb":3000,"charging":False})
        self.assertEqual(self.b.claim(self.worker)["reason"],"remote_device_foreground_priority")
        self.b.edge_availability(self.worker,{"platform":"Windows","architecture":"arm64",
                                         "free_ram_mb":3000,"charging":True})
        with self.b._db() as db:
            db.execute("UPDATE fleet_devices SET last_seen=last_seen-500 WHERE app='samsung-edge'")
            db.commit()
        self.assertEqual(self.b.claim(self.worker)["reason"],"device_telemetry_stale")

    def test_multiple_worker_credentials_must_not_implicitly_enrol(self):
        self.b.issue("samsung-edge","worker",confirm=True)
        with self.assertRaises(ValueError):
            self.b.enroll_edge("samsung-edge","windows_arm64",confirm=True)

if __name__=="__main__":
    unittest.main()
