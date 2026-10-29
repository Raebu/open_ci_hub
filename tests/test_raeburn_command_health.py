"""Production health signalling, receipt verification and tamper fail-closed tests."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import raeburn_command_centre as cc
import raeburn_command_backup as bk
import raeburn_command_health as health

class ProductionHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        for module,name,value in (
            (cc,"STATE",root/"missions"),
            (cc,"DB",root/"missions"/"db.sqlite"),
            (bk,"SOURCE",root/"missions"/"db.sqlite"),
            (bk,"DEST",root/"backups"),
        ):
            patcher=patch.object(module,name,value)
            patcher.start();self.addCleanup(patcher.stop)

    def test_receipt_recalculation_and_corruption(self):
        ident=cc.submit("Review deployment evidence and status",mode="observe")["id"]
        cc.run_once(executor=lambda *a:{"ok":True,"output":"checked"})
        record=cc.inspect(ident)["steps"][0]
        self.assertTrue(record["receipt_valid"])
        self.assertRegex(record["receipt"],r"^[a-f0-9]{64}$")
        self.assertEqual(health._receipt_integrity()["invalid"],0)
        with cc.db() as db:
            db.execute("UPDATE steps SET summary=? WHERE mission=? AND ord=0",
                       ('{"ok":true,"output":"corrupted"}',ident))
        self.assertFalse(cc.inspect(ident)["steps"][0]["receipt_valid"])
        self.assertEqual(health._receipt_integrity()["invalid"],1)

    def test_completed_receipt_corruption_blocks_future_execution(self):
        old=cc.submit("Review CI receipt accuracy",mode="observe")["id"]
        cc.run_once(executor=lambda *a:{"ok":True})
        with cc.db() as db:
            db.execute("UPDATE steps SET summary=? WHERE mission=? AND ord=0",
                       ('{"ok":false}',old))
        self.assertTrue(cc.audit_valid())
        with self.assertRaises(PermissionError):
            cc.submit("Review new product delivery risks",mode="observe")
        with self.assertRaises(PermissionError):
            cc.run_once(executor=lambda *a:{"ok":True})
        self.assertEqual(health._receipt_integrity()["invalid"],1)

    def test_hash_chain_tampering_blocks_queue_controls_and_workers(self):
        ident=cc.submit("Review production deployment state",mode="observe")["id"]
        with cc.db() as db:
            db.execute("UPDATE audit SET detail='forged' WHERE seq=1")
        self.assertFalse(cc.audit_valid())
        with self.assertRaises(PermissionError):cc.submit("Review funding opportunities",mode="observe")
        with self.assertRaises(PermissionError):cc.run_once(executor=lambda *a:{"ok":True})
        with self.assertRaises(PermissionError):cc.control(ident,"pause",confirm=True)

    def test_oversize_evidence_fails_closed_and_retry_is_limited(self):
        ident=cc.submit("Review information from latest tests",mode="observe")["id"]
        result=cc.run_once(executor=lambda *a:{"ok":True,"large":"A"*9000})
        self.assertEqual(result["state"],"failed_step")
        self.assertIsNone(cc.inspect(ident)["steps"][0]["receipt"])
        self.assertTrue(cc.audit_valid())

    def test_failed_executor_false_not_counted_as_success(self):
        ident=cc.submit("Review business monitoring evidence",mode="observe")["id"]
        result=cc.run_once(executor=lambda *a:{"ok":False,"error":"missing"})
        self.assertEqual(result["state"],"failed_step")
        self.assertEqual(cc.inspect(ident)["mission"]["status"],"failed")

    def test_duplicate_key_requires_same_actor_and_tenant(self):
        first=cc.submit("Review business operations today",
                        mode="observe",actor="person-1",key="same-key-missions-1")
        with self.assertRaises(ValueError):
            cc.submit("Review business operations today",mode="observe",
                      actor="person-2",key="same-key-missions-1")
        self.assertTrue(cc.submit("Review business operations today",mode="observe",
                  actor="person-1",key="same-key-missions-1")["duplicate"])

    def test_health_signals_offsite_gap_not_full_approval(self):
        opts={k:True for k in health.SERVICE_UNITS}
        backup={"present":True,"integrity_verified":True,"separate_filesystem":True,
                "fresh":True,"age_hours":2,"offsite_verified":False}
        receipt={"checked":3,"invalid":0,"needs_reconciliation":0,"failed_missions":0}
        result=health.health(units=opts,backup_data=backup,receipt_data=receipt)
        self.assertTrue(result["local_release_ready"])
        self.assertFalse(result["disaster_recovery_complete"])
        self.assertIn("encrypted_offsite_recovery_unverified",result["attention_required"])

    def test_health_flags_missing_local_backup_and_invalid_receipts(self):
        opts={k:True for k in health.SERVICE_UNITS}
        opts["raeburn-command-worker.timer"]=False
        backup={"present":False,"integrity_verified":False,"separate_filesystem":False,
                "fresh":False,"age_hours":None,"offsite_verified":False}
        receipt={"checked":2,"invalid":1,"needs_reconciliation":0,"failed_missions":0}
        result=health.health(units=opts,backup_data=backup,receipt_data=receipt)
        self.assertFalse(result["local_release_ready"])
        self.assertIn("step_receipts_unverifiable",result["attention_required"])
        self.assertIn("required_local_services_missing",result["attention_required"])

    def test_midstep_audit_change_blocks_recording_result(self):
        ident=cc.submit("Review product deployment blockers",mode="observe")["id"]
        def sabotage(*args):
            with cc.db() as db:db.execute("UPDATE audit SET detail='changed' WHERE seq=1")
            return {"ok":True}
        with self.assertRaises(PermissionError):
            cc.run_once(executor=sabotage)
        self.assertEqual(cc.inspect(ident)["steps"][0]["status"],"running")
        self.assertIsNone(cc.inspect(ident)["steps"][0]["receipt"])

if __name__=="__main__":unittest.main()
