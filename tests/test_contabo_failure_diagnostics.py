"""Contabo failure receipts disclose bounded identifiers, not arbitrary test output."""
import contextlib
import hashlib
import io
import json
import sys
import tarfile
import unittest
from unittest.mock import patch

import contabo_ci as ci


def source_wire(failing):
    with io.BytesIO() as buffer:
        with tarfile.open(fileobj=buffer,mode="w") as tar:
            entries={"snapshot/app_runtime.py":b"# isolated fixture\n",
                     "snapshot/tests/__init__.py":b"",
                     "snapshot/tests/test_broken.py":
                     (b"import unittest\nclass DiagnosticTests(unittest.TestCase):\n"
                      b" def test_error(self):\n"
                      +(b"  import oci_intentionally_missing_test_dependency\n" if failing
                        else b"  self.assertTrue(True)\n"))}
            entries.update({f"snapshot/docs/fixture_{i}.txt":b"public fixture"
                            for i in range(51)})
            for name,content in entries.items():
                member=tarfile.TarInfo(name)
                member.size=len(content)
                tar.addfile(member,io.BytesIO(content))
        archive=buffer.getvalue()
    header=json.dumps({"sha":"a"*40,
        "archive_sha256":hashlib.sha256(archive).hexdigest()},
        separators=(",",":")).encode()
    return len(header).to_bytes(4,"big")+header+archive


class BoundedDiagnosticsTests(unittest.TestCase):
    def run_worker(self,failing):
        captured=io.StringIO()
        # worker reads binary wire, emits just a short machine-readable receipt.
        with patch.object(sys,"stdin",io.TextIOWrapper(io.BytesIO(source_wire(failing)))),\
             contextlib.redirect_stdout(captured):
            rc=ci.worker()
        data=json.loads(captured.getvalue())
        return rc,data,captured.getvalue()

    def test_missing_dependency_is_bounded_and_no_output_leaked(self):
        rc,data,raw=self.run_worker(True)
        self.assertNotEqual(rc,0)
        self.assertFalse(data["success"])
        self.assertEqual(data["sha"],"a"*40)
        self.assertEqual(data["missing_dependencies"],
                         ["oci_intentionally_missing_test_dependency"])
        self.assertEqual(data["tests"],1)
        self.assertIn("failed_test_labels",data)
        self.assertNotIn("Traceback",raw)
        self.assertNotIn("File ",raw)
        self.assertLess(len(raw),1800)

    def test_incomplete_suite_is_not_false_positive(self):
        rc,data,raw=self.run_worker(False)
        self.assertNotEqual(rc,0)
        self.assertFalse(data["success"])
        self.assertEqual(data["tests"],1)
        self.assertEqual(data["missing_dependencies"],[])
        self.assertLess(len(raw),1800)

    def test_network_policy_is_one_remote_shell_safe_argument(self):
        value='"--property=RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6"'
        self.assertIn(value,ci.remote_argv())
        self.assertIn("--property=PrivateNetwork=yes",ci.remote_argv())
        self.assertNotIn("sudo",ci.remote_argv())
