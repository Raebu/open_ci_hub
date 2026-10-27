"""Bounded trusted-main Contabo Python suite execution (no generic shell/CI)."""
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch,MagicMock

import contabo_ci as ci

def archive(entries):
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode="w") as tar:
        for path,body in entries:
            item=tarfile.TarInfo(path)
            item.size=len(body)
            tar.addfile(item,io.BytesIO(body))
    return output.getvalue()

class TrustedContaboCITests(unittest.TestCase):
    def test_remote_isolation_no_docker_or_network(self):
        argv=ci.remote_argv()
        for required in (
            "--property=DynamicUser=yes","--property=PrivateNetwork=yes",
            "--property=ProtectHome=yes","--property=ProtectSystem=strict",
            "--property=NoNewPrivileges=yes","--property=MemoryMax=1250M",
            "--property=CPUQuota=150%","--property=TasksMax=128"
        ):
            self.assertIn(required,argv)
        self.assertNotIn("docker",argv)
        self.assertNotIn("bash",argv)
        self.assertIn("--worker",argv)

    def test_bounded_exact_source_archive(self):
        blob=archive([("snapshot/app_runtime.py",b"# ok\n"),
                      ("snapshot/tests/test_a.py",b"import unittest\n")])
        with tempfile.TemporaryDirectory() as folder:
            count=ci._unpack(blob,Path(folder))
            self.assertEqual(count,2)
            self.assertTrue((Path(folder)/"tests/test_a.py").exists())

    def test_github_metadata_allowed_git_database_denied(self):
        with tempfile.TemporaryDirectory() as folder:
            blob=archive([("snapshot/.github/workflows/test.yml",b"name: ci\n"),
                          ("snapshot/app_runtime.py",b"# ok\n"),
                          ("snapshot/tests/test_a.py",b"pass\n")])
            self.assertEqual(ci._unpack(blob,Path(folder)),3)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                ci._unpack(archive([("snapshot/.git/config",b"bad")]),Path(folder))

    def test_archive_traversal_and_symlinks_rejected(self):
        for name in ("snapshot/../etc/passwd","/snapshot/app_runtime.py",
                     "badrepo/app_runtime.py","snapshot/.git/config"):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):
                    ci._unpack(archive([(name,b"danger")]),Path(folder))
        output=io.BytesIO()
        with tarfile.open(fileobj=output,mode="w") as tar:
            info=tarfile.TarInfo("snapshot/tests/link")
            info.type=tarfile.SYMTYPE
            info.linkname="/etc/passwd"
            tar.addfile(info)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                ci._unpack(output.getvalue(),Path(folder))

    def test_no_untrusted_branch_or_dirty_source(self):
        with patch.object(ci,"_git",side_effect=["feature"]):
            with self.assertRaises(RuntimeError):
                ci.dispatch(Path("/tmp"))
        with patch.object(ci,"_git",side_effect=["main"," M source.py"]):
            with self.assertRaises(RuntimeError):
                ci.dispatch(Path("/tmp"))

    def test_stdout_receipt_must_match_exact_sha(self):
        with patch.object(ci,"_git",side_effect=["main","","f"*40,f"{'f'*40}\trefs/heads/main"]), \
            patch.object(ci.subprocess,"run") as run:
            archive_result=MagicMock(returncode=0,stdout=b"z"*2000)
            remote_result=MagicMock(returncode=0,stdout=b'{"success":true,"sha":"wrong","tests":850}')
            run.side_effect=[archive_result,remote_result]
            with self.assertRaises(RuntimeError):
                ci.dispatch(Path("/tmp"))
