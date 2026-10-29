"""Command Centre production gates: no unsanctioned execution or evidence claims."""
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import raeburn_command_centre as cc

class CommandCentreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path=Path(self.tmp.name)
        for name,value in (("STATE",path/"state"),("DB",path/"state"/"missions.db")):
            p=patch.object(cc,name,value)
            p.start();self.addCleanup(p.stop)

    def test_modes_and_plain_language_planning(self):
        plan=cc.plan("Finish Raeburn Talent production release",mode="autonomous")
        self.assertEqual(plan["lane"],"product")
        self.assertTrue(plan["approval_needed"])
        self.assertEqual(plan["steps"][-1],"owner.handoff")
        self.assertFalse(plan["can_execute_external_action"])
        obs=cc.plan("What is the state of our business today?",mode="observe")
        self.assertNotIn("ai.brief",obs["steps"])
        self.assertNotIn("owner.handoff",obs["steps"])
        with self.assertRaises(ValueError):cc.plan("business","automatic")

    def test_objective_data_minimization(self):
        for value in ("short","Email person@company.com with details",
                      "Please use api_key=abc123 for task",
                      "Pay +44 7700 900123 for deployment"):
            with self.subTest(value=value),self.assertRaises(ValueError):cc.clean_objective(value)
        self.assertEqual(cc.clean_objective("  Monitor   projects   today  "), "Monitor projects today")

    def test_owner_idempotency_and_private_modes(self):
        a=cc.submit("Review my latest operational priorities",
                    mode="assist",key="known-request-123")
        b=cc.submit("Review my latest operational priorities",
                    mode="assist",key="known-request-123")
        self.assertEqual(a["id"],b["id"])
        self.assertTrue(b["duplicate"])
        with self.assertRaises(ValueError):
            cc.submit("Review something else entirely",mode="assist",key="known-request-123")
        with self.assertRaises(PermissionError):
            cc.submit("Review another company's information",tenant="not-raeburn")
        self.assertEqual(cc.list_missions()[0]["steps_total"],4)
        self.assertEqual(cc.DB.stat().st_mode & 0o777,0o600)

    def test_safe_steps_are_receipted_and_auditable(self):
        item=cc.submit("Review my latest operational priorities",mode="observe")
        calls=[]
        def worker(kind,lane,objective):
            calls.append(kind)
            return {"ok":True,"kind":kind,"metric":5}
        for _ in range(3):
            result=cc.run_once(executor=worker)
            self.assertEqual(result["state"],"completed_step")
            self.assertRegex(result["receipt"],"^[0-9a-f]{64}$")
        self.assertEqual(cc.inspect(item["id"])["mission"]["status"],"completed")
        self.assertEqual(len(calls),3)
        self.assertEqual(cc.run_once(executor=worker)["state"],"idle")
        self.assertTrue(cc.audit_valid())
        self.assertEqual(cc.list_missions()[0]["steps_completed"],3)

    def test_external_action_is_never_automatically_executed(self):
        ident=cc.submit("Deploy the production CRM and publish release",mode="autonomous")["id"]
        kinds=[]
        for _ in range(4):
            cc.run_once(executor=lambda kind,lane,objective:(kinds.append(kind) or {"healthy":True}))
        result=cc.run_once(executor=lambda *a: (_ for _ in ()).throw(
            AssertionError("external action executed")))
        self.assertEqual(result["state"],"awaiting_approval")
        self.assertEqual(cc.inspect(ident)["mission"]["status"],"awaiting_approval")
        with self.assertRaises(PermissionError):
            cc.control(ident,"approve_handoff",confirm=False)
        approved=cc.control(ident,"approve_handoff",confirm=True)
        self.assertEqual(approved["status"],"handoff_ready")
        self.assertFalse(approved["external_action_performed"])
        self.assertEqual(cc.run_once()["state"],"idle")
        self.assertNotIn("owner.handoff",kinds)

    def test_failed_task_never_claims_success_and_needs_explicit_retry(self):
        ident=cc.submit("Review projects after test failures",mode="observe")["id"]
        result=cc.run_once(executor=lambda *a: (_ for _ in ()).throw(
            RuntimeError("secret should never leak to response")))
        self.assertEqual(result["state"],"failed_step")
        detail=cc.inspect(ident)
        self.assertEqual(detail["mission"]["status"],"failed")
        self.assertNotIn("secret",str(detail))
        self.assertEqual(cc.run_once()["state"],"idle")
        cc.control(ident,"retry_safe",confirm=True)
        reply=cc.run_once(executor=lambda *a:{"ok":True})
        self.assertEqual(reply["state"],"completed_step")
        self.assertTrue(cc.audit_valid())

    def test_second_failed_attempt_blocks_further_retry(self):
        ident=cc.submit("Review business after first failure",mode="observe")["id"]
        for i in range(2):
            cc.run_once(executor=lambda *a: (_ for _ in ()).throw(RuntimeError()))
            if i==0:cc.control(ident,"retry_safe",confirm=True)
        with self.assertRaises(RuntimeError):
            cc.control(ident,"retry_safe",confirm=True)

    def test_pause_cancel_do_not_interrupt_running_work(self):
        ident=cc.submit("Review operational queue health",mode="observe")["id"]
        self.assertEqual(cc.control(ident,"pause",confirm=True)["status"],"paused")
        self.assertEqual(cc.run_once()["state"],"idle")
        self.assertEqual(cc.control(ident,"resume",confirm=True)["status"],"queued")
        with cc.db() as db:
            db.execute("UPDATE steps SET status='running' WHERE mission=? AND ord=0",(ident,))
        with self.assertRaises(RuntimeError): cc.control(ident,"pause",confirm=True)
        with self.assertRaises(RuntimeError): cc.control(ident,"cancel",confirm=True)

    def test_interrupted_running_step_is_not_replayed(self):
        m1=cc.submit("Review product security backlog",mode="observe")["id"]
        with cc.db() as db:
            db.execute("UPDATE steps SET status='running',started=10 WHERE mission=? AND ord=0",(m1,))
            db.execute("UPDATE missions SET status='running' WHERE id=?",(m1,))
        m2=cc.submit("Review funding opportunities today",mode="observe")["id"]
        r=cc.run_once(clock=lambda:300,executor=lambda *a:{"ok":True})
        self.assertEqual(r["id"],m2)
        self.assertEqual(cc.inspect(m1)["mission"]["status"],"needs_reconciliation")
        self.assertEqual(cc.inspect(m1)["steps"][0]["status"],"interrupted")
        cc.control(m1,"reconcile",confirm=True)
        self.assertEqual(cc.inspect(m1)["mission"]["status"],"needs_review")

    def test_audit_tampering_detected(self):
        cc.submit("Review recent sales opportunity evidence",mode="observe")
        self.assertTrue(cc.audit_valid())
        with cc.db() as db:
            db.execute("UPDATE audit SET detail='tampered' WHERE seq=1")
        self.assertFalse(cc.audit_valid())

    def test_daily_proactive_brief_is_idempotent(self):
        a=cc.create_daily_brief("20261029")
        b=cc.create_daily_brief("20261029")
        self.assertEqual(a["id"],b["id"])
        self.assertTrue(b["duplicate"])
        with self.assertRaises(ValueError):cc.create_daily_brief("yesterday")
        self.assertEqual(len(cc.list_missions()),1)

    def test_evidence_priority_signals_are_counts_only(self):
        root=Path(self.tmp.name)
        source=root/".local"/"business-execution"
        source.mkdir(parents=True)
        import sqlite3
        with sqlite3.connect(source/"control.sqlite") as conn:
            conn.execute("CREATE TABLE work(lane TEXT,stage TEXT)")
            conn.executemany("INSERT INTO work VALUES(?,?)", [
                ("product","issue_needs_evidence"),
                ("product","issue_needs_evidence"),
                ("sales","requires_provenance_and_pecr_review"),
                ("coo","complete")
            ])
        with patch.object(cc,"ROOT",root):
            data=cc.attention_signals()
        self.assertEqual(data[0]["lane"],"product")
        self.assertEqual(data[0]["count"],2)
        self.assertEqual(len(data),2)
        self.assertFalse(data[0]["verified_outcome"])
        self.assertNotIn("source_ref",str(data))

    def test_concurrent_claim_is_single_worker(self):
        ident=cc.submit("Review local software health",mode="observe")["id"]
        with cc.db() as db:
            db.execute("UPDATE steps SET status='running',started=? WHERE mission=? AND ord=0",
                       (cc.now(),ident))
        result=cc.run_once()
        self.assertEqual(result["state"],"busy")

if __name__=="__main__": unittest.main()
