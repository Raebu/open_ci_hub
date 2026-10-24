"""Personal WhatsApp safety tests: official composer, explicit owner send only."""
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

from direct_pc_mcp import prepare_personal_whatsapp_message, invoke_tool

ROOT = Path(__file__).resolve().parents[1]


class WhatsAppPersonalTests(unittest.TestCase):
    def test_dashboard_uses_one_personal_whatsapp_composer(self):
        html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="workspace"', html)
        self.assertIn('id="whatsapp-compose"', html)
        self.assertIn('id="whatsapp-phone"', html)
        self.assertIn('id="whatsapp-message"', html)
        self.assertIn('id="whatsapp-copy"', html)
        self.assertIn('id="whatsapp-open"', html)
        self.assertIn('src="/whatsapp-personal.js"', html)
        self.assertEqual(html.count('href="#whatsapp-compose"'), 1)
        self.assertNotIn('id="wa-to"', html)
        self.assertIn("press Send", html)

    def test_pc_tool_requires_confirmation_before_opening(self):
        prepared = prepare_personal_whatsapp_message("+44 (7700) 900123", "Hello there!")
        self.assertEqual(prepared["status"], "prepared")
        self.assertEqual(prepared["url"], "https://wa.me/447700900123?text=Hello%20there%21")
        self.assertFalse(prepared["sent"])
        with self.assertRaises(PermissionError):
            prepare_personal_whatsapp_message(
                "+44 7700 900123", "Hello", open_on_desktop=True)
        with patch("direct_pc_mcp.desktop_open_url", return_value={"launched": True}) as opener:
            result = invoke_tool("prepare_personal_whatsapp_message", {
                "recipient": "+44 7700 900123", "message": "Hello",
                "open_on_desktop": True, "confirm_open": True})
        self.assertTrue(result["opened"])
        self.assertFalse(result["sent"])
        self.assertEqual(opener.call_count, 1)
        self.assertTrue(opener.call_args.args[0].startswith(
            "https://wa.me/447700900123?text="))

    def test_pc_tool_rejects_invalid_numbers_and_blank_text(self):
        for phone in ("", "07700 900123", "javascript:alert(1)", "00 44 7700 900123"):
            with self.subTest(phone=phone):
                with self.assertRaises(ValueError):
                    prepare_personal_whatsapp_message(phone, "Hi")
        with self.assertRaises(ValueError):
            prepare_personal_whatsapp_message("+44 7700 900123", "  ")

    @unittest.skipUnless(shutil.which("node"), "Node.js not installed")
    def test_personal_whatsapp_javascript(self):
        result = subprocess.run(
            ["node", "--test", "tests/test_whatsapp_personal.cjs"],
            cwd=ROOT, capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
