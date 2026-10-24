"""Local Linux namespace policy regressions; no external browser or network."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from autonomy_sandbox import browser_argv, SandboxUnavailable


class SandboxTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.browser = root / "browser"
        self.artifacts = root / "output"
        self.browser.mkdir()
        self.artifacts.mkdir()
        (self.browser / "browser.mjs").write_text("console.log('ok')", encoding="utf-8")
        self.job = root / "plan.json"
        self.job.write_text('{"bounded":true}', encoding="utf-8")
        self.job.chmod(0o600)

    def test_explicit_mounts_hide_personal_workspaces(self):
        argv = browser_argv(self.browser, self.artifacts, plan_file=self.job)
        self.assertIn("--unshare-user", argv)
        self.assertIn("--unshare-pid", argv)
        self.assertIn("--clearenv", argv)
        self.assertIn("--ro-bind", argv)
        self.assertNotIn("/home/theraeburngroup", argv)
        self.assertNotIn("/mnt/kingston", [x for x in argv if x in
            ("/mnt/kingston", "/home/theraeburngroup")])
        self.assertIn("/input/plan.json", argv)
        self.assertEqual(argv[-2:], ["/usr/bin/node", "/app/browser.mjs"])
        self.assertEqual(argv.count("--bind"), 1)

    def test_missing_bwrap_fails_closed(self):
        with self.assertRaises(SandboxUnavailable):
            browser_argv(self.browser, self.artifacts, bwrap="/not/installed/bwrap")

    def test_symlinked_job_and_artifact_rejected(self):
        link=self.artifacts.parent/"linked"
        link.symlink_to(self.artifacts, target_is_directory=True)
        with self.assertRaises(SandboxUnavailable):
            browser_argv(self.browser, link)
        joblink=self.artifacts.parent/"joblink"
        joblink.symlink_to(self.job)
        with self.assertRaises(SandboxUnavailable):
            browser_argv(self.browser, self.artifacts, plan_file=joblink)

    @unittest.skipUnless(Path("/usr/bin/bwrap").is_file(), "Linux bubblewrap not available")
    def test_real_namespace_hides_home_and_reads_plan(self):
        argv = browser_argv(self.browser, self.artifacts, plan_file=self.job)
        argv[-2:] = [
            "/usr/bin/python3", "-c",
            "from pathlib import Path;"
            "print(Path('/home').exists(),"
            "Path('/home/theraeburngroup').exists(),"
            "Path('/app/browser.mjs').is_file(),"
            "Path('/input/plan.json').read_text())"
        ]
        p = subprocess.run(argv, capture_output=True, text=True, timeout=8, check=False)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), 'True False True {"bounded":true}')


if __name__ == "__main__":
    unittest.main()
