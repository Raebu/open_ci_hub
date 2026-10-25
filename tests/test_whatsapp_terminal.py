"""Regression checks for opt-in personal WhatsApp terminal integration."""
from pathlib import Path
import json
import shutil
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
INTEGRATION=ROOT/"integrations"/"whatsapp-personal"

class WhatsAppTerminalTests(unittest.TestCase):
    def test_auto_reply_off_by_default_and_no_secrets_committed(self):
        pkg=json.loads((INTEGRATION/"package.json").read_text())
        self.assertTrue(pkg["private"])
        self.assertIn("whatsapp-web.js",pkg["dependencies"])
        self.assertTrue((INTEGRATION/".gitignore").exists())
        src=(INTEGRATION/"index.js").read_text()
        self.assertIn("autoSend:false",src)
        self.assertIn("approvedChats:[]",src)
        self.assertIn("chatAllowed",src)
        self.assertIn("msg.fromMe",src)
        self.assertFalse((INTEGRATION/"config.json").exists())
        self.assertFalse((INTEGRATION/".wwebjs_auth").exists())

    @unittest.skipUnless(shutil.which("node"),"Node.js unavailable")
    def test_js_policy_and_local_model(self):
        p=subprocess.run(["node","--test","test/policy.test.js"],cwd=INTEGRATION,
                         capture_output=True,text=True,timeout=20,check=False)
        self.assertEqual(p.returncode,0,p.stdout+"\n"+p.stderr)

if __name__=="__main__":
    unittest.main()
