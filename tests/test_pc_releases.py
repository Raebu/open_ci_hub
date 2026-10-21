"""Non-root PC releases: archive validation, immutable staging and recovery."""
from __future__ import annotations
import hashlib
import io
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import pc_releases as pr


class PcReleasesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "releases"
        a = patch.object(pr, "ROOT", self.root)
        b = patch.object(pr, "_owner")
        a.start()
        b.start()
        self.addCleanup(a.stop)
        self.addCleanup(b.stop)
        pr.enrol("demo", confirm=True)
        self.sha_a = "a" * 40
        self.sha_b = "b" * 40

    def archive(self, entries=None):
        entries = entries or {"dist/index.html": "<h1>Good</h1>"}
        target = self.base / ("input-" + str(len(list(self.base.glob("input*")))) + ".zip")
        with zipfile.ZipFile(target, "w") as file:
            for name, data in entries.items():
                file.writestr(name, data)
        return target, hashlib.sha256(target.read_bytes()).hexdigest()

    def stage(self, sha=None, archive=None, digest=None):
        if archive is None:
            archive, digest = self.archive()
        return pr.stage("demo", archive=str(archive),
                        commit=sha or self.sha_a, expected_sha256=digest)

    def test_enrol_requires_explicit_confirmation_and_no_replacement(self):
        with self.assertRaises(PermissionError):
            pr.enrol("another")
        with self.assertRaises(FileExistsError):
            pr.enrol("demo", confirm=True)
        with self.assertRaises(ValueError):
            pr.enrol("../bad", confirm=True)
        self.assertFalse(pr.status("demo")["current_commit"])

    def test_stage_verified_by_sha_and_reject_mismatches(self):
        archive, digest = self.archive()
        with self.assertRaises(PermissionError):
            self.stage(archive=archive, digest="f" * 64)
        result = self.stage(archive=archive, digest=digest)
        self.assertEqual(result["status"], "staged")
        path = self.root / "demo" / "releases" / self.sha_a / "dist" / "index.html"
        self.assertEqual(path.read_text(), "<h1>Good</h1>")
        self.assertEqual(self.stage(archive=archive, digest=digest)["status"],
                         "already_staged")
        another, another_digest = self.archive({"dist/index.html": "other"})
        with self.assertRaises(PermissionError):
            self.stage(archive=another, digest=another_digest)

    def test_forbid_zip_traversal_absolute_hidden_and_symlinks(self):
        cases = [{"../stolen.txt": "secret"}, {"/tmp/evil": "x"},
                 {".env": "SECRET"}, {"file": "x", "file/bad": "y"}]
        for entries in cases:
            with self.subTest(entries=entries):
                archive, digest = self.archive(entries)
                with self.assertRaises(PermissionError):
                    self.stage(archive=archive, digest=digest)
        link = zipfile.ZipInfo("link.txt")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive = self.base / "symlink.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr(link, "../../private")
        with self.assertRaises(PermissionError):
            self.stage(archive=archive, digest=hashlib.sha256(archive.read_bytes()).hexdigest())

    def test_archive_limits_and_exact_commit_are_required(self):
        archive, digest = self.archive({"dist/data.txt": "x" * 8})
        with self.assertRaises(ValueError):
            self.stage(sha="main", archive=archive, digest=digest)
        original = pr.MAX_EXTRACT_BYTES
        with patch.object(pr, "MAX_EXTRACT_BYTES", 4):
            with self.assertRaises(PermissionError):
                self.stage(archive=archive, digest=digest)
        self.assertEqual(pr.MAX_EXTRACT_BYTES, original)

    def test_promote_requires_owner_confirmation_and_rolls_back(self):
        self.stage()
        second, digest = self.archive({"dist/index.html": "<h1>New</h1>"})
        self.stage(sha=self.sha_b, archive=second, digest=digest)
        with self.assertRaises(PermissionError):
            pr.promote("demo", self.sha_a)
        result = pr.promote("demo", self.sha_a, confirm=True)
        self.assertEqual(result["status"], "promoted")
        self.assertEqual(pr.status("demo")["current_commit"], self.sha_a)
        result = pr.promote("demo", self.sha_b, confirm=True)
        self.assertEqual(result["previous_commit"], self.sha_a)
        self.assertEqual(pr.status("demo")["current_commit"], self.sha_b)
        with self.assertRaises(PermissionError):
            pr.rollback("demo")
        rolled = pr.rollback("demo", confirm=True)
        self.assertEqual(rolled["current_commit"], self.sha_a)
        self.assertEqual(pr.status("demo")["current_commit"], self.sha_a)

    def test_failed_health_check_restores_previous_pointer(self):
        self.stage()
        second, digest = self.archive({"dist/index.html": "<h1>New</h1>"})
        self.stage(sha=self.sha_b, archive=second, digest=digest)
        pr.promote("demo", self.sha_a, confirm=True)
        config = pr._config("demo")
        config["health_port"] = 8765
        from hub import secure_write
        import json
        secure_write(self.root / "demo" / "config.json", json.dumps(config))
        with patch.object(pr, "_healthy", side_effect=[False, True]):
            with self.assertRaisesRegex(RuntimeError, "previous pointer restored"):
                pr.promote("demo", self.sha_b, confirm=True)
        self.assertEqual(pr.status("demo")["current_commit"], self.sha_a)

    def test_failed_service_restart_restores_previous_pointer(self):
        self.stage()
        second, digest = self.archive({"dist/index.html": "new"})
        self.stage(sha=self.sha_b, archive=second, digest=digest)
        pr.promote("demo", self.sha_a, confirm=True)
        config = pr._config("demo")
        config["service"] = "open-ci-hub-app-demo.service"
        from hub import secure_write
        import json
        secure_write(self.root / "demo" / "config.json", json.dumps(config))
        with patch.object(pr, "_restart", side_effect=[RuntimeError("start failed"), None]):
            with self.assertRaises(RuntimeError):
                pr.promote("demo", self.sha_b, confirm=True)
        self.assertEqual(pr.status("demo")["current_commit"], self.sha_a)

    def test_refuse_current_link_outside_managed_releases(self):
        folder = self.root / "demo"
        (folder / "current").symlink_to("../../secret")
        with self.assertRaises(PermissionError):
            pr.status("demo")
        self.stage()
        with self.assertRaises(PermissionError):
            pr.promote("demo", self.sha_a, confirm=True)


if __name__ == "__main__":
    unittest.main()
