"""Personal WhatsApp composer: official link only, no automated send."""
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

from direct_pc_mcp import prepare_personal_whatsapp_message, invoke_tool

ROOT = Path(__file__).resolve().parents[1]

class WhatsAppPersonalTests(unittest.TestCase):
    def test_authenticated_dashboard_contains_personal_composer(self):
        html=(ROOT/"dashboard"/"index.html").read_text(encoding="utf-8")
        self.assertIn('id="workspace"',html)
        self.assertIn('id="whatsapp"',html)
        self.assertIn('id="wa-to"',html)
        self.assertIn('id="wa-message"',html)
        self.assertIn('id="wa-copy"',html)
        self.assertIn('id="wa-open"',html)
        self.assertIn('rel="noopener noreferrer"',html)
        self.assertIn('press Send',html)

    def test_pc_tool_requires_confirmation_before_opening(self):
        prepared=prepare_personal_whatsapp_message("+44 (7700) 900123","Hello there!")
        self.assertEqual(prepared["status"],"prepared")
        self.assertEqual(prepared["url"],"https://wa.me/447700900123?text=Hello%20there%21")
        self.assertFalse(prepared["sent"])
        with self.assertRaises(PermissionError):
            prepare_personal_whatsapp_message("+44 7700 900123","Hello",open_on_desktop=True)
        with patch("direct_pc_mcp.desktop_open_url", return_value={"launched":True}) as opener:
            result=invoke_tool("prepare_personal_whatsapp_message",{
                "recipient":"+44 7700 900123","message":"Hello",
                "open_on_desktop":True,"confirm_open":True})
        self.assertTrue(result["opened"])
        self.assertFalse(result["sent"])
        self.assertEqual(opener.call_count,1)
        self.assertTrue(opener.call_args.args[0].startswith("https://wa.me/447700900123?text="))

    def test_pc_tool_rejects_invalid_numbers_and_blank_text(self):
        for phone in ("", "07700 900123", "javascript:alert(1)", "00 44 7700 900123"):
            with self.subTest(phone=phone):
                with self.assertRaises(ValueError):
                    prepare_personal_whatsapp_message(phone,"Hi")
        with self.assertRaises(ValueError):
            prepare_personal_whatsapp_message("+44 7700 900123","  ")

    @unittest.skipUnless(shutil.which("node"), "Node.js not installed")
    def test_javascript_validates_number_and_prefills_without_sending(self):
        node = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const code=fs.readFileSync('dashboard/app.js','utf8');
const sandbox={document:{addEventListener:()=>{}},navigator:{}};
vm.createContext(sandbox);
vm.runInContext(code,sandbox,{timeout:3000});
const link=sandbox.whatsappPersonalLink('+44 (7700) 900123','Hello there!');
assert.strictEqual(link,'https://wa.me/447700900123?text=Hello%20there!');
assert.strictEqual(sandbox.whatsappPersonalLink('15551234567','Hi!'),'https://wa.me/15551234567?text=Hi!');
for(const number of ['javascript:alert(1)','07700 900123','00 44 7700 900123','','+1-abc','127.0.0.1']){
  assert.throws(()=>sandbox.whatsappPersonalLink(number,'Hi'));
}
assert.throws(()=>sandbox.whatsappPersonalLink('+447700900123',''));
assert.throws(()=>sandbox.whatsappPersonalLink('+447700900123','a'.repeat(1801)));
assert(!code.includes('send_whatsapp_template('));
console.log('WhatsApp composer validation OK');
"""
        result=subprocess.run(["node","-e",node],cwd=ROOT,capture_output=True,
                              text=True,timeout=10,check=False)
        self.assertEqual(result.returncode,0,result.stderr)

if __name__=="__main__":
    unittest.main()
