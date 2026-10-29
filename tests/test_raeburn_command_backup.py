"""Separate-volume SQLite snapshots: recovery, permissions and audit integrity."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import raeburn_command_centre as cc
import raeburn_command_backup as backup

class CommandBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        pairs=[
            (cc,"STATE",root/"live"),
            (cc,"DB",root/"live"/"missions.sqlite3"),
            (backup,"SOURCE",root/"live"/"missions.sqlite3"),
            (backup,"DEST",root/"different-disk"/"backups")
        ]
        for module,name,value in pairs:
            p=patch.object(module,name,value)
            p.start();self.addCleanup(p.stop)
        self.mission=cc.submit("Review local source evidence",mode="observe")["id"]
        cc.run_once(executor=lambda *a:{"ok":True,"check":"passed"})

    def test_snapshot_restore_integrity_without_touching_live_db(self):
        original=cc.DB.read_bytes()
        result=backup.backup()
        self.assertEqual(result["status"],"verified_local_backup")
        self.assertTrue(result["receipts"]["audit_chain"])
        self.assertEqual(result["receipts"]["missions"],1)
        file=backup.DEST/result["filename"]
        self.assertEqual(file.stat().st_mode & 0o777,0o600)
        self.assertEqual(backup.DEST.stat().st_mode & 0o777,0o700)
        manifest=json.loads((backup.DEST/(result["filename"]+".json")).read_text())
        self.assertEqual(manifest["sha256"],result["digest"])
        self.assertEqual(original,cc.DB.read_bytes())
        self.assertTrue(cc.audit_valid())

    def test_tampered_backup_fails_recovery_integrity(self):
        result=backup.backup()
        file=backup.DEST/result["filename"]
        with sqlite3.connect(file) as conn:
            conn.execute("UPDATE audit SET detail='modified_after_receipt' WHERE seq=1")
            conn.commit()
        with self.assertRaises(RuntimeError):
            backup.verify(file)
        self.assertTrue(cc.audit_valid())

    def test_backup_refuses_symlinked_destination(self):
        target=backup.DEST
        target.parent.mkdir(parents=True)
        target.symlink_to(Path(self.tmp.name))
        with self.assertRaises(PermissionError):
            backup.backup()

if __name__=="__main__":unittest.main()
