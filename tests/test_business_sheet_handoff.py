"""Canonical Google Sheet readback is private, bounded and never execution authority."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import business_execution as be
from tests.test_business_execution import config


class SheetHandoffTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.tmp=Path(folder.name)
        self.bridge=be.Bridge(state=self.tmp/"state",config=config())
        self.file=self.bridge.state/"my-tasks-source.json"

    def snapshot(self,**changes):
        data={"schema":"raeburn.canonical-sheet-cache.v1",
              "spreadsheet_id":"EXISTING-TASKS","tab":"My Tasks",
              "observed_at":datetime.now(timezone.utc).isoformat(),
              "rows":[{"row":15,"lane":"funding","status":"BLOCKED",
                       "priority":"Critical","has_external_evidence":True}]}
        data.update(changes)
        self.file.write_text(json.dumps(data),encoding="utf8")
        self.file.chmod(0o600)

    def test_fresh_is_read_only_and_not_a_verified_completion(self):
        self.snapshot()
        result=self.bridge.sheet_snapshots()
        self.assertEqual(result["status"],"fresh")
        self.assertEqual(result["rows"],1)
        work=self.bridge.snapshot()["recent"]
        self.assertTrue(any(w["kind"]=="canonical_task" and
                            w["stage"]=="reported_requires_external_readback"
                            for w in work))
        self.assertNotIn(b"SUBMITTED VERIFIED",self.bridge.file.read_bytes())
        self.assertFalse(any(x["cutover_allowed"]
                             for x in self.bridge.readiness()["lanes"]))

    def test_expired_rows_do_not_count_as_current(self):
        old=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
        self.snapshot(observed_at=old)
        self.assertEqual(self.bridge.sheet_snapshots()["status"],"expired")
        self.assertFalse(any(w["kind"]=="canonical_task"
                             for w in self.bridge.snapshot()["recent"]))

    def test_wrong_sheet_duplicate_rows_and_untrusted_columns_rejected(self):
        self.snapshot(spreadsheet_id="WRONG")
        self.assertEqual(self.bridge.sheet_snapshots()["status"],"invalid")
        rows=[{"row":2,"lane":"sales","status":"OPEN",
               "priority":"High","has_external_evidence":False}]*2
        self.snapshot(rows=rows)
        self.assertEqual(self.bridge.sheet_snapshots()["status"],"invalid")
        rows=[{"row":2,"lane":"sales","status":"OPEN","priority":"High",
               "has_external_evidence":False,"email":"secret@other.example"}]
        self.snapshot(rows=rows)
        self.assertEqual(self.bridge.sheet_snapshots()["status"],"invalid")
        self.assertNotIn(b"secret@other",self.bridge.file.read_bytes())

    def test_private_permissions_and_symlink_refused(self):
        self.snapshot()
        self.file.chmod(0o644)
        self.assertEqual(self.bridge.sheet_snapshots()["status"],"unsafe")
        self.file.unlink()
        outside=self.tmp/"other"
        outside.write_text("{}")
        self.file.symlink_to(outside)
        self.assertEqual(self.bridge.sheet_snapshots()["status"],"unavailable")
        self.assertEqual(outside.read_text(),"{}")


if __name__=="__main__":
    unittest.main()
