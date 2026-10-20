"""Safe local workspace MCP regression tests. All workspaces are temporary."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import coding_workspace as ws
import direct_pc_mcp


class CodingWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        parent = Path(self.tmp.name)
        self.project = parent / "my-project"
        self.project.mkdir()
        self.local = parent / "private"
        self.local.mkdir()
        self.registry = self.local / "workspaces.json"
        self.receipts = self.local / "receipts.jsonl"
        self.patchers = [
            patch.object(ws, "REGISTRY", self.registry),
            patch.object(ws, "RECEIPTS", self.receipts),
            patch.object(ws, "_require_user"),
        ]
        for item in self.patchers:
            item.start()
            self.addCleanup(item.stop)
        ws._save({"project": {"root": str(self.project), "write": False,
                              "checks": {}}})

    def set_entry(self, *, write=False, checks=None):
        ws._save({"project": {"root": str(self.project),
                              "write": write, "checks": checks or {}}})

    def test_workspace_discovery_and_listing(self):
        (self.project / "main.py").write_text("print(1)\n")
        (self.project / ".env").write_text("SECRETS")
        result = ws.workspaces()
        self.assertEqual(result["workspaces"][0]["name"], "project")
        listed = ws.list_files("project")["entries"]
        self.assertEqual([v["name"] for v in listed], ["main.py"])

    def test_owner_write_gate_and_compare_and_swap(self):
        with self.assertRaises(PermissionError):
            ws.write_file("project", "main.py", "x = 1\n", "")
        self.set_entry(write=True)
        created = ws.write_file("project", "main.py", "x = 1\n", "")
        self.assertEqual(created["result"], "verified")
        self.assertEqual(created["sha256"],
                         hashlib.sha256(b"x = 1\n").hexdigest())
        self.assertEqual(ws.read_file("project", "main.py")["content"], "x = 1\n")
        with self.assertRaises(PermissionError):
            ws.write_file("project", "main.py", "hijack", "")
        with self.assertRaises(PermissionError):
            ws.write_file("project", "main.py", "hijack", "0" * 64)
        changed = ws.write_file("project", "main.py", "x = 2\n", created["sha256"])
        self.assertEqual(ws.read_file("project", "main.py")["sha256"], changed["sha256"])
        lines = self.receipts.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[-1])["operation"], "write")
        self.assertNotIn("x = 2", lines[-1])

    def test_no_traversal_hidden_binary_oversize_or_symlink(self):
        self.set_entry(write=True)
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("private")
        (self.project / "escape.txt").symlink_to(outside)
        (self.project / "secret.bin").write_bytes(b"hello\x00world")
        for path in ("../outside.txt", ".git/config", ".env", "node_modules/a",
                     "/etc/passwd", "foo/../bar.txt"):
            with self.subTest(path=path), self.assertRaises(
                    (ValueError, PermissionError, OSError)):
                ws.read_file("project", path)
        with self.assertRaises(OSError):
            ws.read_file("project", "escape.txt")
        with self.assertRaises(PermissionError):
            ws.read_file("project", "secret.bin")
        with self.assertRaises(ValueError):
            ws.write_file("project", "too-big.txt", "x" * (ws.MAX_FILE + 1), "")

    def test_write_creates_only_inside_existing_directories(self):
        self.set_entry(write=True)
        with self.assertRaises(FileNotFoundError):
            ws.write_file("project", "not-there/x.py", "x = 1", "")
        (self.project / "src").mkdir()
        ws.write_file("project", "src/hello.py", "print('ok')", "")
        self.assertEqual((self.project / "src" / "hello.py").read_text(), "print('ok')")

    def test_allowlisted_check_receipt_is_real_exit_code(self):
        self.set_entry(checks={"unit": [sys.executable, "-c",
                                        "print('test pass')"]})
        result = ws.run_check("project", "unit")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["exit_code"], 0)
        self.assertNotIn("test pass", json.dumps(result))
        receipt = json.loads(self.receipts.read_text().splitlines()[0])
        self.assertEqual(receipt["exit_code"], 0)
        with self.assertRaises(PermissionError):
            ws.run_check("project", "unknown")

    def test_failed_check_is_not_reported_as_success(self):
        self.set_entry(checks={"fail": [sys.executable, "-c",
                                        "raise SystemExit(4)"]})
        result = ws.run_check("project", "fail")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exit_code"], 4)

    def test_mcp_rejects_missing_or_extra_args(self):
        with self.assertRaises(ValueError):
            direct_pc_mcp.invoke_tool("write_coding_file",
                {"workspace": "project", "path": "ok.txt", "content": "text"})
        with self.assertRaises(ValueError):
            direct_pc_mcp.invoke_tool("run_coding_check",
                {"workspace": "project", "check": "unit", "shell": "sudo"})
        response = direct_pc_mcp.invoke_tool("list_coding_workspaces", {})
        self.assertEqual(response["workspaces"][0]["name"], "project")


if __name__ == "__main__":
    unittest.main()
