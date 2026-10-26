"""Standing owner authorisation for automatic, read-only cached browser sessions."""
from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import browser_session_grants as grants
from browser_session_vault import save
from autonomy_core import Journal
from autonomy_cli import run_one

KEY=b"G"*32
SITE="example.com"
STATE={"cookies":[{"name":"example","value":"dummy-not-a-live-cookie",
                   "domain":SITE,"secure":True}],
       "origins":[{"origin":"https://"+SITE,"localStorage":[]}]}


def read_plan(host=SITE):
    return {"id":"auto-grant-test","hosts":[host],
            "limits":{"max_cost_usd":0,"max_seconds":25,"allow_browser_interaction":False},
            "steps":[{"id":"visit","kind":"browse","needs":[],
                      "input":{"url":"https://"+host+"/private",
                               "actions":[{"type":"screenshot"}]}}]}


class StandingGrantTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder=Path(tmp.name)/"vault"
        save(SITE,STATE,key=KEY,folder=self.folder,now=1000,ttl=86400,confirm=True)

    def test_explicit_approval_and_authenticated_record(self):
        with self.assertRaises(PermissionError):
            grants.authorise(SITE,folder=self.folder,key=KEY,now=1000)
        consent=grants.authorise(SITE,folder=self.folder,key=KEY,
                                now=1000,days=30,confirm=True)
        self.assertTrue(consent["auto_reuse"])
        self.assertTrue(grants.enabled(SITE,folder=self.folder,key=KEY,now=1001))
        path=self.folder/(SITE+".autouse.json")
        self.assertEqual(path.stat().st_mode & 0o077,0)
        self.assertNotIn("dummy-not-a-live-cookie",path.read_text())
        with self.assertRaises(FileExistsError):
            grants.authorise(SITE,folder=self.folder,key=KEY,now=1001,confirm=True)
        self.assertEqual(grants.list_grants(folder=self.folder,key=KEY,now=1002)[0]["site"],SITE)
        self.assertFalse(grants.enabled("else.example.com",folder=self.folder,key=KEY,now=1002))

    def test_expiry_tamper_revocation_and_wrong_key_fail_closed(self):
        grants.authorise(SITE,folder=self.folder,key=KEY,now=1000,days=1,confirm=True)
        with self.assertRaises(PermissionError):
            grants.enabled(SITE,folder=self.folder,key=KEY,now=1000+86401)
        with self.assertRaises(PermissionError):
            grants.enabled(SITE,folder=self.folder,key=b"x"*32,now=1001)
        path=self.folder/(SITE+".autouse.json")
        record=json.loads(path.read_text())
        record["grant"]["scope"]="all_actions"
        path.write_text(json.dumps(record))
        with self.assertRaises(PermissionError):
            grants.enabled(SITE,folder=self.folder,key=KEY,now=1001)
        with self.assertRaises(PermissionError):
            grants.revoke(SITE,folder=self.folder)
        grants.revoke(SITE,folder=self.folder,confirm=True)
        self.assertFalse(grants.enabled(SITE,folder=self.folder,key=KEY,now=1001))
        self.assertTrue((self.folder/(SITE+".session.enc")).exists())

    def test_only_single_site_read_only_browse(self):
        grants.authorise(SITE,folder=self.folder,key=KEY,now=1000,confirm=True)
        plan=read_plan()
        step=plan["steps"][0]
        self.assertEqual(grants.select_host(plan,step,folder=self.folder,
                                            key=KEY,now=1002),SITE)
        plan["hosts"].append("another.com")
        self.assertIsNone(grants.select_host(plan,step,folder=self.folder,
                                             key=KEY,now=1002))
        plan=read_plan()
        plan["limits"]["allow_browser_interaction"]=True
        self.assertIsNone(grants.select_host(plan,step,folder=self.folder,
                                             key=KEY,now=1002))
        plan=read_plan()
        plan["steps"][0]["input"]["actions"]=[{"type":"click","selector":"a"}]
        self.assertIsNone(grants.select_host(plan,plan["steps"][0],folder=self.folder,
                                             key=KEY,now=1002))
        plan=read_plan()
        plan["steps"][0]["input"]["url"]="http://example.com/private"
        self.assertIsNone(grants.select_host(plan,plan["steps"][0],folder=self.folder,
                                             key=KEY,now=1002))

    def test_background_mission_uses_approved_session_without_per_step_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            journal=Journal(Path(td)/"state")
            journal.submit(read_plan(),owner_approved=True)
            seen=[]
            def browser(plan,step,artifact,**kw):
                seen.append(kw)
                return {"ok":True,"records":[]}
            with patch("browser_session_grants.select_host",return_value=SITE),patch(
                 "browser_session_vault.load",return_value=STATE):
                response=run_one(journal,"auto-grant-test",
                    sensor=lambda:{"mem_available_mb":8000,"load_per_core":.1,
                                   "max_thermal_c":40},browser=browser)
            self.assertEqual(response["status"],"completed")
            self.assertEqual(seen[0]["session_host"],SITE)

    def test_unavailable_auto_session_defers_without_consuming_lease(self):
        with tempfile.TemporaryDirectory() as td:
            journal=Journal(Path(td)/"state")
            journal.submit(read_plan(),owner_approved=True)
            with patch("browser_session_grants.select_host",return_value=SITE),patch(
                 "browser_session_vault.load",side_effect=PermissionError("expired")):
                response=run_one(journal,"auto-grant-test",
                     sensor=lambda:{"mem_available_mb":8000,"load_per_core":.1,
                                    "max_thermal_c":40})
            self.assertEqual(response["status"],"deferred")
            self.assertEqual(response["reason"],"saved_login_expired_or_unavailable")
            self.assertEqual(journal.snapshot("auto-grant-test")["steps"][0]["status"],"queued")


if __name__=="__main__":
    unittest.main()
