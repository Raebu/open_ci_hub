"""Regression coverage for signed evidence, visual plan boundaries and GUI limits."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from autonomy_core import Journal,PolicyError
from autonomy_attestation import make_key,manifest_from_journal,sign_manifest,verify_receipt
from autonomy_planner import compose
from virtual_desktop import xauthority,start

class SignedReceiptTests(unittest.TestCase):
    def test_end_to_end_signature(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            with self.assertRaises(PermissionError):
                make_key(base/"key")
            pub=make_key(base/"key",confirmed=True)
            with self.assertRaises(FileExistsError):
                make_key(base/"key",confirmed=True)
            j=Journal(base/"state")
            p={"id":"evidence-test","hosts":[],"limits":{"max_cost_usd":0,
               "max_seconds":30,"allow_browser_interaction":False},
               "steps":[{"id":"hash","kind":"digest","needs":[],"input":{"text":"test"}}]}
            j.submit(p,owner_approved=True)
            with self.assertRaises(RuntimeError):
                manifest_from_journal(j,"evidence-test")
            j.start_step("evidence-test","hash")
            j.finish_step("evidence-test","hash",ok=True,result={"ok":True,"bytes":4})
            m=manifest_from_journal(j,"evidence-test")
            path=base/"receipt.json"
            sign_manifest(m,base/"key",path,confirmed=True)
            self.assertTrue(verify_receipt(path,expected_public_key=pub))
            self.assertFalse(verify_receipt(path,expected_public_key="1"*64))
            record=json.loads(path.read_text())
            record["manifest"]["status"]="failed"
            path.write_text(json.dumps(record))
            self.assertFalse(verify_receipt(path,expected_public_key=pub))

class PlanningTests(unittest.TestCase):
    def fixture(self):
        return {"id":"screen-test","hosts":["example.com"],"limits":{
            "max_cost_usd":0,"max_seconds":40,"allow_browser_interaction":False},
            "steps":[{"id":"visit","kind":"browse","needs":[],"input":{
                 "url":"https://example.com/","actions":[{"type":"screenshot"}]}}]}
    def test_owner_scoped_plan(self):
        q="Capture only a screenshot from the site"
        self.assertEqual(compose(q,["example.com"],planner=lambda d,h:self.fixture())["id"],"screen-test")
        bad=self.fixture();bad["hosts"].append("evil.com")
        with self.assertRaises(PolicyError):
            compose(q,["example.com"],planner=lambda d,h:bad)
        bad=self.fixture();bad["limits"]["max_cost_usd"]=1
        with self.assertRaises(PolicyError):
            compose(q,["example.com"],planner=lambda d,h:bad)
        bad=self.fixture();bad["steps"][0]["input"]["actions"]=[{"type":"click","selector":"body"}]
        with self.assertRaises(PolicyError):
            compose(q,["example.com"],planner=lambda d,h:bad)

class VirtualDisplayTests(unittest.TestCase):
    def test_separate_screen_and_owner_guard(self):
        from struct import unpack
        auth=xauthority(":91",bytes(range(16)))
        self.assertEqual(unpack(">H",auth[:2])[0],256)
        self.assertIn(b"MIT-MAGIC-COOKIE-1",auth)
        with self.assertRaises(PermissionError):
            start()
        with patch("virtual_desktop.admit_local",return_value=(False,"interactive_cpu_pressure")):
            self.assertEqual(start(confirm=True)["reason"],"interactive_cpu_pressure")
