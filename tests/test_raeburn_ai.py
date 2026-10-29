"""RaeburnAI isolated transaction learning, privacy, tenancy, training approval tests."""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import subprocess
from unittest.mock import patch

import raeburn_ai as ai

SHA="a"*64

class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.st=root/"raeburn-ai"
        p1=patch.object(ai,"DB_DIR",self.st)
        p2=patch.object(ai,"DB_FILE",self.st/"memory.sqlite")
        p3=patch.object(ai,"BUSINESS_DB",root/"source.sqlite")
        for p in (p1,p2,p3):
            p.start()
            self.addCleanup(p.stop)

    def test_sensitive_data_fails_closed(self):
        for text in [
            "Customer contact somebody@example.com",
            "Please use password=secretvalue123",
            "Call +44 7700 900123",
            "Card 4111 1111 1111 1111"
        ]:
            with self.subTest(text=text):
                with self.assertRaises(PermissionError): ai.clean(text)
        self.assertFalse(ai.DB_FILE.exists())

    def test_dedupe_and_company_scoped_search(self):
        one=ai.remember("raeburn-group","technical_lesson",
                        "ARM64 CI tests should use a sandboxed worker",verified=True)
        two=ai.remember("raeburn-group","technical_lesson",
                        "ARM64 CI tests should use a sandboxed worker",verified=True)
        ai.remember("another-company","technical_lesson",
                    "ARM64 CI uses a different deployment schedule",verified=True)
        self.assertTrue(one["stored"])
        self.assertFalse(two["stored"])
        group=ai.retrieval("raeburn-group","ARM64 deployment tests")
        self.assertEqual(len(group),1)
        self.assertIn("sandboxed worker",group[0]["fact"])
        self.assertNotIn("different",str(group))

    def test_observed_not_training_without_explicit_review(self):
        self.assertTrue(ai.ingest_event("raeburn-group","business_execution","42",
                   "transaction_transition","sales operation approved stage -> complete",SHA))
        self.assertFalse(ai.retrieval("raeburn-group","sales approved"))
        self.assertEqual(len(ai.retrieval("raeburn-group","sales approved",
                                          include_observations=True)),1)
        self.assertEqual(ai.training_preview("raeburn-group")["approved_examples"],0)
        with self.assertRaises(PermissionError):
            ai.ingest_event("raeburn-group","business_execution","43",
                  "transaction_transition","sales approved",SHA,verified=True)

    def test_real_ingestion_copies_only_stage_and_hash(self):
        uri=ai.BUSINESS_DB
        with closing(sqlite3.connect(uri)) as source:
            source.executescript("""
            CREATE TABLE work (id TEXT PRIMARY KEY,lane TEXT,kind TEXT,source_ref TEXT,detail TEXT);
            CREATE TABLE event (id INTEGER PRIMARY KEY,work_id TEXT,old_stage TEXT,
                  new_stage TEXT,evidence_sha256 TEXT,created REAL);
            """)
            source.execute("INSERT INTO work VALUES (?,?,?,?,?)",
              ("internal_work","sales","proposal","sensitive@example.com",
               '{"private":"4111111111111111"}'))
            source.execute("INSERT INTO event VALUES (?,?,?,?,?,?)",
              (1,"internal_work","new","submitted",SHA,12345))
            source.commit()
        result=ai.sync_business_transactions()
        self.assertEqual(result["ingested"],1)
        self.assertEqual(ai.sync_business_transactions()["ingested"],0)
        self.assertEqual(ai.status()["memory_events"],1)
        with ai.db_connection() as db:
            f=db.execute("SELECT fact,source_id FROM memory").fetchone()
            self.assertIn("submitted",f["fact"])
            self.assertNotIn("@",f["fact"])
            self.assertNotIn("411111",f["fact"])
            self.assertNotIn("internal_work",f["fact"])
        with closing(sqlite3.connect(uri)) as source:
            self.assertEqual(source.execute("SELECT COUNT(*) FROM event").fetchone()[0],1)

    def test_governed_training_examples_do_not_update_weights(self):
        ai.ingest_event("raeburn-group","operator_approved","receipt_proven",
                   "approved_outcome","CI receipt was independently reviewed",SHA,
                   verified=True,approved=True)
        result=ai.propose_training_example("raeburn-group",
                    "What does a completed CI receipt prove?",
                    "The job completed but requires acceptance checks.",SHA,"reviewer_1")
        self.assertTrue(result["accepted"])
        self.assertEqual(ai.training_preview("raeburn-group")["approved_examples"],1)
        self.assertFalse(ai.training_preview("raeburn-group")["fine_tune_eligible"])
        self.assertFalse(result["weights_changed"])

    def test_approved_training_export_has_real_jsonl_and_heldout_split(self):
        for i in range(30):
            sha=f"{i:02x}"+"a"*62
            ai.ingest_event("raeburn-group","operator_approved",f"event_{i}",
                     "technical_lesson",f"Reviewed event {i} passed checks",sha,
                     verified=True,approved=True)
            res=ai.propose_training_example("raeburn-group",
                     f"What did event {i} establish?",
                     f"Event {i} passed independent checks.",sha,"reviewer_1")
            self.assertTrue(res["accepted"])
        out=ai.export_training_dataset("raeburn-group")
        self.assertFalse(out["weights_changed"])
        self.assertGreaterEqual(out["validation_count"],3)
        self.assertGreaterEqual(out["train_count"],20)
        tr=next((self.st/"training").glob("*-train.jsonl"))
        val=next((self.st/"training").glob("*-validation.jsonl"))
        self.assertEqual(tr.stat().st_mode & 0o777,0o600)
        self.assertEqual(len(tr.read_text().splitlines()),out["train_count"])
        self.assertEqual(len(val.read_text().splitlines()),out["validation_count"])
        first=json.loads(tr.read_text().splitlines()[0])
        self.assertEqual(first["messages"][0]["role"],"user")
        self.assertEqual(first["messages"][1]["role"],"assistant")

    def test_ask_is_private_and_attaches_citations(self):
        ai.remember("raeburn-group","approved_outcome",
                   "A test deployment on ARM64 passed safety checks",verified=True)
        seen=[]
        def fake(prompt,tokens):
            seen.append(prompt)
            return "The deployment passed [event:1]."
        reply=ai.ask("raeburn-group","ARM64 deployment safety",inference=fake)
        self.assertTrue(reply["ok"])
        self.assertFalse(reply["cloud_used"])
        self.assertEqual(reply["memory_items"],1)
        self.assertIn("[event:1]",seen[0])
        self.assertTrue(reply["answer"].startswith("The deployment"))

    def test_primary_inference_timeout_returns_none_for_safe_local_fallback(self):
        def timeout(*args,**kwargs):
            raise subprocess.TimeoutExpired(args[0],kwargs.get("timeout",52))
        self.assertIsNone(ai._raeburnai_inference("Harmless test",64,runner=timeout))

    def test_storage_permissions(self):
        ai.status()
        self.assertEqual(ai.DB_FILE.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.st.stat().st_mode & 0o777,0o700)

if __name__=="__main__":
    unittest.main()
