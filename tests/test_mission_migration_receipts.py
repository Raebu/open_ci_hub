"""Migration leases must never duplicate execution after an interruption."""
import sqlite3
import unittest
from mission_migration_receipts import claim,complete,reconcile

class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
    def test_claim_complete_once(self):
        job={"kind":"json.inspect","payload":{"text":"{}"}}
        first=claim(self.db,"job_001",job,"garuda","samsung",now=100)
        self.assertEqual(first["claim"],"reserved")
        second=claim(self.db,"job_001",job,"garuda","samsung",now=110)
        self.assertEqual(second["claim"],"already_claimed")
        receipt=complete(self.db,"job_001",{"ok":True},now=120)
        self.assertEqual(receipt["state"],"completed")
        with self.assertRaises(ValueError):
            complete(self.db,"job_001",{"ok":True},now=140)
    def test_stale_migration_needs_owner_reconciliation(self):
        job={"kind":"digest","input":{"text":"abc"}}
        claim(self.db,"job_002",job,"garuda","s25",now=100)
        self.assertEqual(reconcile(self.db,now=225)[0]["status"],
                         "needs_reconciliation_no_automatic_replay")
        self.assertEqual(claim(self.db,"job_002",job,"garuda","s25",now=226)["claim"],
                         "already_claimed")
    def test_mutating_unsupported_and_conflicts(self):
        with self.assertRaises(ValueError):
            claim(self.db,"danger",{"kind":"delete"},"garuda","s25")
        claim(self.db,"job_003",{"kind":"digest","input":{}},"garuda","s25")
        with self.assertRaises(ValueError):
            claim(self.db,"job_003",{"kind":"digest","input":{"text":"other"}},
                  "garuda","s25")
