"""Only owner-approved private Xvfb is allowed for graphical browser tasks."""
import unittest
import tempfile
from pathlib import Path
from autonomy_core import validate_plan,PolicyError
from autonomy_sandbox import browser_argv,SandboxUnavailable

class GraphicalMissionTests(unittest.TestCase):
    def test_only_private_display(self):
        p={"id":"private-screen-test","hosts":["example.com"],
           "limits":{"max_seconds":40,"max_cost_usd":0,
                     "allow_browser_interaction":False},
           "steps":[{"id":"visit","kind":"browse","needs":[],
                     "input":{"url":"https://example.com/",
                     "display":"private_xvfb","actions":[{"type":"screenshot"}]}}]}
        self.assertEqual(validate_plan(p),p)
        for unwanted in (":0","kde","x11","127.0.0.1"):
            p["steps"][0]["input"]["display"]=unwanted
            with self.assertRaises(PolicyError):
                validate_plan(p)
    def test_display_mount_requires_authority_and_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            browser=root/"browser";browser.mkdir()
            (browser/"browser.mjs").write_text("console.log('safe')")
            out=root/"out";out.mkdir()
            normal=browser_argv(browser,out)
            self.assertNotIn("/tmp/.X11-unix/X91",normal)
            self.assertNotIn("XAUTHORITY",normal)
            self.assertIn("--clearenv",normal)
            with self.assertRaises(SandboxUnavailable):
                browser_argv(browser,out,virtual_display=True)
            auth=root/"cookie";auth.write_bytes(b"fake");auth.chmod(0o600)
            fake_socket=root/"X91";fake_socket.touch()
            with self.assertRaises(SandboxUnavailable):
                browser_argv(browser,out,virtual_display=True,
                             authority_file=auth,socket_file=fake_socket)
