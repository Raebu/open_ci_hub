"""Curated project monitoring never scans secrets or follows symlink escapes."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app_runtime import Broker
from edge_pool import EdgePool
import edge_pool_monitor as monitor


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/"open-ci-hub").mkdir()
        self.checkout=self.root/"open-ci-hub"
        self.source=self.checkout/"tiny.json"
        self.source.write_text('{"task":"configuration","valid":true}\n')
        self.manifest=self.root/"manifest.json"
        self.manifest.write_text(json.dumps(["tiny.json"]))
        broker=Broker(self.root/"broker-state")
        broker.register("s25-edge",["json.inspect"],confirm=True)
        broker.set_task_budget("s25-edge","json.inspect",128,confirm=True)
        broker.set_parallel("s25-edge",1,confirm=True)
        worker=broker.auth(broker.issue("s25-edge","worker",confirm=True)["token"])
        broker.enroll_edge("s25-edge","android_arm64",confirm=True)
        broker.edge_availability(worker,{"platform":"Android","architecture":"arm64",
                                         "free_ram_mb":3400,"charging":True})
        self.pool=EdgePool(broker,devices=["s25-edge"])

    def test_manifest_dispatches_once_per_content_and_no_duplicate(self):
        with patch.object(monitor,"PROJECTS_ROOT",self.root),patch.object(monitor,"ROOT",self.checkout):
            a=monitor.run(self.manifest,pool=self.pool)
            self.assertEqual(len(a["dispatched"]),1)
            self.assertFalse(a["dispatched"][0]["duplicate"])
            b=monitor.run(self.manifest,pool=self.pool)
            self.assertTrue(b["dispatched"][0]["duplicate"])
            self.source.write_text('{"task":"configuration","valid":false}\n')
            c=monitor.run(self.manifest,pool=self.pool)
            self.assertFalse(c["dispatched"][0]["duplicate"])
        with self.pool.broker._db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],2)

    def test_secret_is_rejected(self):
        self.source.write_text('{"api_key": "secret-token-here"}')
        with patch.object(monitor,"PROJECTS_ROOT",self.root),patch.object(monitor,"ROOT",self.checkout):
            result=monitor.run(self.manifest,pool=self.pool)
        self.assertFalse(result["dispatched"])
        self.assertEqual(len(result["skipped"]),1)

    def test_symlinks_and_outside_folders_are_rejected(self):
        self.source.unlink()
        self.source.symlink_to("/etc/passwd")
        with patch.object(monitor,"PROJECTS_ROOT",self.root),patch.object(monitor,"ROOT",self.checkout):
            result=monitor.run(self.manifest,pool=self.pool)
        self.assertFalse(result["dispatched"])
        self.assertEqual(len(result["skipped"]),1)

    def test_missing_manifest_causes_no_work(self):
        self.assertTrue(monitor.run(self.root/"not-there",pool=self.pool)["manifest_missing"])


if __name__=="__main__":
    unittest.main()
