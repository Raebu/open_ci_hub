"""Native Razer sales snapshot and ChatGPT handoff acceptance tests."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import raeburn_sales_native as native

NOW=datetime(2026,10,28,9,15,tzinfo=timezone.utc).timestamp()


def setup_leads(path):
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE companies(
          id INTEGER PRIMARY KEY, status TEXT, company_fit_score REAL,
          contact_quality_score REAL, company_number TEXT, source_url TEXT,
          review_status TEXT, needs_manual_validation INTEGER);
        CREATE TABLE contacts(id INTEGER PRIMARY KEY, company_id INTEGER,
          source_url TEXT, email TEXT);
        CREATE TABLE job_runs(id INTEGER PRIMARY KEY,status TEXT);
        INSERT INTO companies VALUES(
          4,'enriched',0.78,0.65,'12345678',
          'https://example.org/source','unreviewed',1);
        INSERT INTO companies VALUES(
          8,'discovered',NULL,NULL,NULL,
          'https://other.example','approved',0);
        INSERT INTO contacts VALUES(1,4,'https://source.example','person@secret.example');
        INSERT INTO contacts VALUES(2,8,NULL,'second@secret.example');
        INSERT INTO job_runs VALUES(1,'completed');
        """)


def setup_provider(path):
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE sync(account TEXT PRIMARY KEY,provider TEXT,
                          state TEXT,seen INTEGER,last_checked REAL,explanation TEXT);
        CREATE TABLE event(id TEXT,account TEXT,classification TEXT);
        CREATE TABLE hold(identity_hash TEXT PRIMARY KEY,reason TEXT);
        INSERT INTO sync VALUES('raeburn-primary','gmail',
          'needs_independent_provider_oauth_or_api_key',0,0,'');
        INSERT INTO event VALUES('1','raeburn-primary','outbound');
        INSERT INTO hold VALUES('abc','provider_suppression');
        """)


def setup_outbox(path):
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE job(state TEXT);
        CREATE TABLE social_job(stage TEXT);
        INSERT INTO job VALUES('dispatch_uncertain');
        INSERT INTO social_job VALUES('queued');
        """)


def setup_sheet(path):
    data={"schema":"raeburn.canonical-sheet-cache.v1",
          "spreadsheet_id":"safe-id","tab":"My Tasks",
          "observed_at":datetime.fromtimestamp(NOW-120,timezone.utc).isoformat(),
          "rows":[
              {"row":2,"lane":"sales","status":"Open","priority":"High",
               "has_external_evidence":False},
              {"row":3,"lane":"coo","status":"Open","priority":"Low",
               "has_external_evidence":False},
          ]}
    path.write_text(json.dumps(data))
    path.chmod(0o600)


class NativeSalesTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.state=self.root/"private"
        self.state.mkdir(mode=0o700)
        self.leads=self.root/"leads.sqlite"
        setup_leads(self.leads)
        setup_provider(self.state/"channel-readback.sqlite")
        setup_outbox(self.state/"outbound.sqlite")
        setup_sheet(self.state/"my-tasks-source.json")
        self.feed=native.SalesFeed(self.state,clock=lambda:NOW)
        self.cfg={"lead_db":"leads.sqlite"}

    def build(self):
        with patch.object(native,"PROJECTS",self.root):
            return self.feed.build(config=self.cfg)

    def test_source_backed_event_and_no_plaintext_prospect_data(self):
        report=self.build()
        self.assertEqual(report["source_status"],"degraded")
        self.assertEqual(report["metrics"]["lead_source"]["companies"],2)
        self.assertEqual(report["metrics"]["lead_source"]["contacts"],2)
        self.assertEqual(report["metrics"]["lead_source"]["enriched"],1)
        self.assertEqual(report["metrics"]["lead_source"]["needs_manual_review"],1)
        self.assertFalse(report["autonomous_native_email_ready"])
        self.assertEqual(report["verified_direct_client_emails_sent_today"],None)
        self.assertTrue(report["material_change"])
        self.assertEqual(report["event_cursor"],1)
        output=self.feed.path.read_bytes()
        for secret in (b"person@secret.example",b"second@secret.example",
                       b"example.org/source",b"secret",b"data/autoposter.sqlite"):
            self.assertNotIn(secret,output)
        self.assertEqual(self.feed.path.stat().st_mode&0o777,0o600)

    def test_unchanged_sources_do_not_spam_chatgpt_event_log(self):
        one=self.build()
        two=self.build()
        self.assertEqual(one["event_cursor"],two["event_cursor"])
        self.assertFalse(two["material_change"])
        with self.feed.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM event").fetchone()[0],1)

    def test_time_passing_alone_is_not_a_material_sales_event(self):
        first=self.build()
        later=native.SalesFeed(self.state,clock=lambda:NOW+90)
        with patch.object(native,"PROJECTS",self.root):
            second=later.build(config=self.cfg)
        self.assertFalse(second["material_change"])
        self.assertEqual(second["event_cursor"],first["event_cursor"])

    def test_provider_suppression_and_uncertain_sends_prioritised(self):
        result=self.build()
        queue=result["recommended_next_actions"]
        names={x["action"] for x in queue}
        self.assertIn("reconcile_uncertain_provider_send",names)
        self.assertIn("reconcile_suppression_across_channels",names)
        self.assertIn("use_connected_chatgpt_outreach_until_native_oauth_ready",names)
        self.assertEqual(result["metrics"]["native_dispatch"]
                         ["uncertain_sends_require_reconciliation"],1)
        self.assertEqual(result["metrics"]["mailbox"]
                         ["provider_suppression_holds"],1)

    def test_feed_expires_and_never_claims_fresh_when_old(self):
        self.build()
        self.assertTrue(self.feed.handoff()["feed_fresh"])
        later=native.SalesFeed(self.state,clock=lambda:NOW+native.MAX_AGE+1)
        self.assertFalse(later.handoff()["feed_fresh"])
        self.assertEqual(later.handoff()["status"],"stale")

    def test_lead_source_unavailable_blocks_outreach(self):
        self.leads.unlink()
        result=self.build()
        self.assertEqual(result["source_status"],"degraded")
        self.assertFalse(result["source_coverage"]["lead_database"])
        self.assertEqual(result["metrics"]["lead_source"]
                         ["verified_eligible_to_email"],0)

    def test_source_read_only_does_not_modify_canonical_sqlite(self):
        self.build()
        with sqlite3.connect(self.leads) as db:
            count=db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        self.assertEqual(count,2)
        self.assertFalse(self.leads.with_name(self.leads.name+"-wal").exists())

    def test_disallows_source_path_traversal(self):
        with self.assertRaises(PermissionError):
            self.feed.build(config={"lead_db":"../../secrets.sqlite"})

    def test_never_labels_accepted_as_delivered(self):
        with sqlite3.connect(self.state/"outbound.sqlite") as db:
            db.execute("INSERT INTO job VALUES('accepted_pending_delivery')")
        result=self.build()
        self.assertEqual(result["native_accepted_not_confirmed_delivered"],1)
        self.assertIsNone(result["verified_direct_client_emails_sent_today"])
        self.assertIsNone(result["verified_cash_collected_today"])


if __name__=="__main__":
    unittest.main()
