"""Direct-PC MCP contract, local consent, bounded file access and action tests."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import direct_pc_mcp as mcp


class DirectMcpTests(unittest.TestCase):
    def request(self, method, rid=1, params=None):
        envelope = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            envelope["params"] = params
        return mcp.mcp_response(envelope)

    def test_tools_discovered_and_schemas_are_closed(self):
        response = self.request("tools/list")
        names = {x["name"] for x in response["result"]["tools"]}
        self.assertEqual(names, {
            "get_pc_status", "inspect_ci_failure", "create_pc_document",
            "list_pc_directory", "read_pc_file", "write_pc_file",
            "run_pc_command", "install_arch_packages", "desktop_open_url",
            "desktop_launch_app", "desktop_screenshot", "desktop_list_windows",
            "desktop_window_action", "desktop_type_text", "desktop_press_key",
            "desktop_click", "prepare_personal_whatsapp_message",
            "hub_dialogue_read", "hub_dialogue_reply",
        })
        for tool in response["result"]["tools"]:
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
        self.assertTrue(all(name in names for name in (
            "list_pc_directory", "read_pc_file", "write_pc_file", "run_pc_command",
            "install_arch_packages", "desktop_screenshot", "desktop_click",
        )))

    def test_initialize_ping_and_notifications(self):
        self.assertEqual(self.request("initialize")["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(self.request("ping")["result"], {})
        self.assertIsNone(mcp.mcp_response({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_tool_call_returns_real_result_and_error(self):
        with patch("direct_pc_mcp.pc_status", return_value={"host_kind": "live_owner_garuda_linux", "disk_free_gb": 21.5}) as task:
            result = self.request("tools/call", params={"name": "get_pc_status", "arguments": {}})
        task.assert_called_once()
        body = json.loads(result["result"]["content"][0]["text"])
        self.assertEqual(body["disk_free_gb"], 21.5)
        self.assertFalse(result["result"]["isError"])
        bad = self.request("tools/call", params={"name": "run_command", "arguments": {"command": "id"}})
        self.assertTrue(bad["result"]["isError"])
        self.assertIn("Unknown MCP tool", bad["result"]["content"][0]["text"])

    def test_screenshot_response_preserves_mcp_image_block(self):
        image = {"type": "image", "data": base64.b64encode(b"png").decode(), "mimeType": "image/png"}
        with patch("direct_pc_mcp.invoke_tool", return_value={"_mcp_content_blocks": [image]}):
            response = self.request("tools/call", params={"name": "desktop_screenshot", "arguments": {}})
        self.assertEqual(response["result"]["content"], [image])
        self.assertFalse(response["result"]["isError"])

    def test_default_and_previous_policies_remain_per_host_opt_in(self):
        with tempfile.TemporaryDirectory() as root:
            policy = Path(root) / "permissions.json"
            self.assertFalse(any(mcp.capability_status(policy).values()))

            legacy = {"schema": mcp.LEGACY_SCHEMA, "document_creation": False}
            policy.write_text(json.dumps(legacy))
            policy.chmod(0o600)
            self.assertFalse(any(mcp.capability_status(policy).values()))

            previous = {"schema": mcp.PREVIOUS_SCHEMA, **{name: False for name in mcp.CAPABILITIES}}
            previous["terminal_execution"] = True
            policy.write_text(json.dumps(previous))
            policy.chmod(0o600)
            migrated = mcp.capability_status(policy)
            self.assertTrue(migrated["terminal_execution"])
            self.assertFalse(migrated["desktop_control"])
            self.assertFalse(migrated["file_edit"])

            policy.chmod(0o644)
            with self.assertRaises(PermissionError):
                mcp.consent_status(policy)

    def test_background_only_lock_overrides_desktop_reenrolment(self):
        with tempfile.TemporaryDirectory() as root:
            from pathlib import Path
            private = Path(root)
            private.chmod(0o700)
            with (patch("direct_pc_mcp.STATE_DIR", private),
                  patch("direct_pc_mcp.BACKGROUND_ONLY", private / "background-only.lock"),
                  patch("direct_pc_mcp.PERMISSIONS", private / "permissions.json")):
                self.assertFalse(mcp.is_background_only())
                mcp.set_background_only(True, confirm=True)
                self.assertTrue(mcp.is_background_only())
                self.assertFalse(mcp.capability_status()["desktop_control"])
                with self.assertRaises(PermissionError):
                    mcp.set_capabilities({"desktop_control": True}, confirm=True)
                with self.assertRaises(PermissionError):
                    mcp.require_capability("desktop_control")
                with self.assertRaises(PermissionError):
                    mcp.set_background_only(False, confirm=True)
                with patch("direct_pc_mcp.sys.stdin.isatty", return_value=True), patch("direct_pc_mcp.sys.stdout.isatty", return_value=True):
                    mcp.set_background_only(False, confirm=True)
                self.assertFalse(mcp.is_background_only())
                self.assertFalse(mcp.capability_status()["desktop_control"])

    def test_local_capability_enrolment_requires_explicit_confirmation(self):
        with patch("direct_pc_mcp.require_nonroot"):
            with self.assertRaises(PermissionError):
                mcp.set_capabilities({"terminal_execution": True}, confirm=False)
            with self.assertRaises(PermissionError):
                mcp.set_consent(True, confirm=False)

    def test_document_write_requires_enrolment(self):
        with patch("direct_pc_mcp.require_nonroot"), \
             patch("direct_pc_mcp.require_capability", side_effect=PermissionError("disabled")):
            with self.assertRaises(PermissionError):
                mcp.create_document("consent.md", "Hello")

    def test_document_creation_is_new_only_and_private(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            (home / "Documents").mkdir(mode=0o700)
            with patch("direct_pc_mcp.require_nonroot"), \
                 patch("direct_pc_mcp.require_capability"), \
                 patch("direct_pc_mcp._audit"), \
                 patch("direct_pc_mcp.Path.home", return_value=home):
                result = mcp.create_document("hello.md", "# Verified document")
                self.assertEqual(result["result"], "created")
                path = home / "Documents" / "OpenCIHubTasks" / "hello.md"
                self.assertEqual(path.read_text(), "# Verified document")
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                with self.assertRaises(FileExistsError):
                    mcp.create_document("hello.md", "overwrite")
                for name in ("../steal.txt", "../../.bashrc", "malicious.py", "foo/hi.md", "a.txt.tmp"):
                    with self.subTest(name=name), self.assertRaises(ValueError):
                        mcp.create_document(name, "no")
                with self.assertRaises(ValueError):
                    mcp.create_document("too-large.txt", "x" * 4001)

    def test_symlinked_document_folder_denied(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            (home / "Documents").mkdir()
            (home / "Documents" / "OpenCIHubTasks").symlink_to(home)
            with patch("direct_pc_mcp.require_nonroot"), \
                 patch("direct_pc_mcp.require_capability"), \
                 patch("direct_pc_mcp.Path.home", return_value=home):
                with self.assertRaises(PermissionError):
                    mcp.create_document("no.md", "test")

    def test_directory_listing_hides_sensitive_paths(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / "Code_Projects"
            project.mkdir()
            (project / "README.md").write_text("safe")
            (project / ".env").write_text("password=secret")
            (project / ".git").mkdir()
            (project / "src").mkdir()
            roots = [{"label": "projects", "path": str(project)}]
            with patch("direct_pc_mcp.require_nonroot"), \
                 patch("direct_pc_mcp.require_capability"), \
                 patch("direct_pc_mcp._audit"), \
                 patch("direct_pc_mcp._roots", return_value=roots):
                result = mcp.list_pc_directory(str(project))
                names = {item["name"] for item in result["entries"]}
                self.assertEqual(names, {"README.md", "src"})
                self.assertEqual(result["root_type"], "projects")
                with self.assertRaises(PermissionError):
                    mcp.list_pc_directory("../")

    def test_file_read_redacts_common_credentials_and_blocks_env(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / "Code_Projects"
            project.mkdir()
            file = project / "README.md"
            file.write_text("password=very-secret-value\nsafe line\n")
            (project / ".env").write_text("PASSWORD=private")
            roots = [{"label": "projects", "path": str(project)}]
            with patch("direct_pc_mcp.require_nonroot"), \
                 patch("direct_pc_mcp.require_capability"), \
                 patch("direct_pc_mcp._audit"), \
                 patch("direct_pc_mcp._roots", return_value=roots):
                result = mcp.read_pc_file(str(file))
                self.assertIn("[REDACTED]", result["file_text"])
                self.assertIn("safe line", result["file_text"])
                self.assertEqual(result["sha256"], hashlib.sha256(file.read_bytes()).hexdigest())
                self.assertGreater(result["redactions_applied"], 0)
                with self.assertRaises(PermissionError):
                    mcp.read_pc_file(str(project / ".env"))

    def test_file_edit_refuses_redacted_content_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / "Code_Projects"
            project.mkdir()
            file = project / "README.md"
            original = "password=very-secret-value\nsafe line\n"
            file.write_text(original)
            original_bytes = file.read_bytes()
            expected_sha = hashlib.sha256(original_bytes).hexdigest()
            roots = [{"label": "projects", "path": str(project)}]
            with patch("direct_pc_mcp.require_nonroot"), \
                 patch("direct_pc_mcp.require_capability"), \
                 patch("direct_pc_mcp._audit"), \
                 patch("direct_pc_mcp._roots", return_value=roots):
                read_result = mcp.read_pc_file(str(file))
                self.assertIn("[REDACTED]", read_result["file_text"])
                self.assertEqual(read_result["sha256"], expected_sha)
                # The exact redacted view must never be written over the source.
                with self.assertRaises(PermissionError):
                    mcp.write_pc_file(str(file), read_result["file_text"] + "changed\n", expected_sha)
                # Removing the placeholder must not silently delete the hidden value either.
                with self.assertRaises(PermissionError):
                    mcp.write_pc_file(str(file), "safe line\nchanged\n", expected_sha)
                self.assertEqual(file.read_bytes(), original_bytes)

    def test_file_edit_refuses_to_overwrite_content_redacted_on_read(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / "Code_Projects"
            project.mkdir()
            file = project / "README.md"
            original = "password=very-secret-value\nsafe line\n"
            file.write_text(original)
            old_sha = hashlib.sha256(file.read_bytes()).hexdigest()
            roots = [{"label": "projects", "path": str(project)}]
            with patch("direct_pc_mcp.require_nonroot"), \
                 patch("direct_pc_mcp.require_capability"), \
                 patch("direct_pc_mcp._audit") as audit, \
                 patch("direct_pc_mcp._private_backup") as backup, \
                 patch("direct_pc_mcp._roots", return_value=roots):
                with self.assertRaisesRegex(PermissionError, "redacted on read"):
                    mcp.write_pc_file(str(file), "password=[REDACTED]\nsafe line edited\n", old_sha)
                self.assertEqual(file.read_text(), original)
                backup.assert_not_called()
                audit.assert_called_once()

    def test_file_edit_uses_hash_guard_and_private_backup_hook(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / "Code_Projects"
            project.mkdir()
            file = project / "app.py"
            file.write_text("print('old')\n")
            old_sha = hashlib.sha256(file.read_bytes()).hexdigest()
            roots = [{"label": "projects", "path": str(project)}]
            with patch("direct_pc_mcp.require_nonroot"), \
                 patch("direct_pc_mcp.require_capability"), \
                 patch("direct_pc_mcp._audit"), \
                 patch("direct_pc_mcp._private_backup", return_value="/private/backup.bak") as backup, \
                 patch("direct_pc_mcp._roots", return_value=roots):
                result = mcp.write_pc_file(str(file), "print('new')\n", old_sha)
                self.assertEqual(file.read_text(), "print('new')\n")
                self.assertEqual(result["backup"], "/private/backup.bak")
                backup.assert_called_once_with(file)
                with self.assertRaises(RuntimeError):
                    mcp.write_pc_file(str(file), "print('bad')\n", old_sha)
                with self.assertRaises(ValueError):
                    mcp.write_pc_file(str(project / "new.py"), "print('new')\n", old_sha)
                created = mcp.write_pc_file(str(project / "new.py"), "print('new')\n", None)
                self.assertEqual(created["result"], "written")
                self.assertEqual((project / "new.py").read_text(), "print('new')\n")

    def test_run_pc_command_executes_as_user_and_returns_exit_output(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / "Code_Projects"
            project.mkdir()
            roots = [{"label": "projects", "path": str(project)}]
            with patch("direct_pc_mcp.require_nonroot"), \
                 patch("direct_pc_mcp.require_capability"), \
                 patch("direct_pc_mcp._audit"), \
                 patch("direct_pc_mcp._roots", return_value=roots):
                result = mcp.run_pc_command("printf OpenCIHubCommandVerified", str(project), 10)
            self.assertEqual(result["exit_code"], 0)
            self.assertIn("OpenCIHubCommandVerified", result["output"])
            self.assertFalse(result["timed_out"])
            self.assertEqual(result["cwd"], str(project))

    def test_install_package_rejects_shell_metacharacters(self):
        with patch("direct_pc_mcp.require_nonroot"), patch("direct_pc_mcp.require_capability"):
            with self.assertRaises(ValueError):
                mcp.install_arch_packages(["kdotool; rm -rf /"])

    def test_output_redacts_exact_local_runtime_secrets(self):
        with patch.dict(os.environ, {"CONTROL_PLANE_API_KEY": "local-secret-unique-value"}):
            value, count = mcp._redact("tool printed local-secret-unique-value by mistake")
        self.assertNotIn("local-secret-unique-value", value)
        self.assertIn("[REDACTED LOCAL SECRET]", value)
        self.assertEqual(count, 1)

    def test_file_edit_rejects_oversized_existing_file_before_hashing(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / "Code_Projects"
            project.mkdir()
            file = project / "large.py"
            file.write_bytes(b"x" * (mcp.MAX_FILE_BYTES + 1))
            roots = [{"label": "projects", "path": str(project)}]
            with patch("direct_pc_mcp.require_nonroot"), \
                 patch("direct_pc_mcp.require_capability"), \
                 patch("direct_pc_mcp._audit"), \
                 patch("direct_pc_mcp._roots", return_value=roots):
                with self.assertRaises(ValueError):
                    mcp.write_pc_file(str(file), "print('new')\n", hashlib.sha256(file.read_bytes()).hexdigest())

    def test_desktop_press_key_uses_argv_not_shell(self):
        with patch("direct_pc_mcp._require_desktop", return_value={"PATH": "/usr/bin"}), \
             patch("direct_pc_mcp.shutil.which", return_value="/usr/bin/wtype"), \
             patch("direct_pc_mcp._run_bounded", return_value={"exit_code": 0, "output": "", "timed_out": False}), \
             patch("direct_pc_mcp._audit"):
            result = mcp.desktop_press_key("s", ["ctrl"])
            self.assertTrue(result["pressed"])
            self.assertIn("-M", mcp._run_bounded.call_args.args[0])

    def test_kde_key_fallback_releases_modifiers(self):
        unsupported = {"exit_code": 1, "output": "Compositor does not support the virtual keyboard protocol"}
        ok = {"exit_code": 0, "output": ""}
        with patch("direct_pc_mcp._require_desktop", return_value={"PATH": "/usr/bin"}), \
             patch("direct_pc_mcp.shutil.which", side_effect=lambda name, **kw: "/usr/bin/" + name), \
             patch("direct_pc_mcp._run_bounded", side_effect=[unsupported, ok]) as run, \
             patch("direct_pc_mcp._audit"):
            self.assertTrue(mcp.desktop_press_key("s", ["ctrl"])["pressed"])
            self.assertEqual(run.call_args.args[0], ["/usr/bin/ydotool", "key", "29:1", "31:1", "31:0", "29:0"])

    def test_kde_text_fallback_uses_stdin_and_literal_ascii(self):
        import subprocess
        unsupported = subprocess.CompletedProcess([], 1, "", "Compositor does not support the virtual keyboard protocol")
        ok = subprocess.CompletedProcess([], 0, "", "")
        with patch("direct_pc_mcp._require_desktop", return_value={"PATH": "/usr/bin"}), \
             patch("direct_pc_mcp.shutil.which", side_effect=lambda name, **kw: "/usr/bin/" + name), \
             patch("direct_pc_mcp.subprocess.run", side_effect=[unsupported, ok]) as run, \
             patch("direct_pc_mcp._audit"):
            text = "Literal $value and backslash \\n"
            self.assertTrue(mcp.desktop_type_text(text)["typed"])
            self.assertEqual(run.call_args.kwargs["input"], text)
            self.assertIn("--file", run.call_args.args[0])
            self.assertEqual(run.call_args.args[0][-1], "-")

    def test_pointer_corner_target_are_separate_frames_before_click(self):
        with patch("direct_pc_mcp._require_desktop", return_value={"PATH": "/usr/bin"}), \
             patch("direct_pc_mcp.shutil.which", return_value="/usr/bin/ydotool"), \
             patch("direct_pc_mcp._run_bounded", return_value={"exit_code": 0, "output": ""}) as run, \
             patch("direct_pc_mcp.time.sleep") as pause, patch("direct_pc_mcp._audit"):
            mcp.desktop_click(534, 912)
            self.assertEqual([c.args[0][1:] for c in run.call_args_list], [
                ["mousemove", "-x", "-32767", "-y", "-32767"],
                ["mousemove", "-x", "534", "-y", "912"], ["click", "0xC0"]])
            self.assertEqual(pause.call_count, 2)

    def test_stdio_discards_invalid_input_and_keeps_notifications_silent(self):
        inputs = "\n".join([
            "not valid json",
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        ]) + "\n"
        output = io.StringIO()
        with patch("direct_pc_mcp.require_nonroot"):
            mcp.serve(io.StringIO(inputs), output)
        items = [json.loads(v) for v in output.getvalue().splitlines()]
        self.assertEqual(len(items), 2)
        self.assertEqual([x["id"] for x in items], [1, 2])

    def test_github_failure_lookup_calls_read_only_native_inspection(self):
        import sys
        import types
        module = types.ModuleType("ci_failure_diagnostics")
        module.diagnose = lambda provider, repo, run, include_logs=False: {
            "provider": provider, "repo": repo, "run": run, "logs_checked": include_logs}
        with patch.dict(sys.modules, {"ci_failure_diagnostics": module}):
            actual = mcp.invoke_tool("inspect_ci_failure", {
                "provider": "github", "repository": "Raebu/open_ci_hub", "run_id": "39460692725"})
        self.assertEqual(actual["repo"], "Raebu/open_ci_hub")
        self.assertTrue(actual["logs_checked"])
        with self.assertRaises(ValueError):
            mcp.invoke_tool("inspect_ci_failure", {
                "provider": "github", "repository": "../../etc", "run_id": "1"})


if __name__ == "__main__":
    unittest.main()
