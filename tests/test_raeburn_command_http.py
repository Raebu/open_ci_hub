"""Authenticated RaeburnAI dashboard controls and CSRF/role isolation."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import raeburn_command_centre as cc
from tests.test_web_console import DashboardHTTPTests

class RaeburnCommandHTTPTests(unittest.TestCase):
    # Reuse the existing HTTP fixture without inheriting and replaying unrelated
    # dashboard test methods, which can collide over shared test identities.
    setUpClass=DashboardHTTPTests.__dict__['setUpClass']
    tearDownClass=DashboardHTTPTests.__dict__['tearDownClass']
    request=DashboardHTTPTests.request
    session=DashboardHTTPTests.session
    def setUp(self):
        self.temp_commands=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_commands.cleanup)
        folder=Path(self.temp_commands.name)/"raeburn"
        for var,value in (("STATE",folder),("DB",folder/"missions.sqlite3")):
            p=patch.object(cc,var,value)
            p.start()
            self.addCleanup(p.stop)

    def test_owner_login_required_for_read_and_create(self):
        self.assertEqual(self.request("GET","/api/raeburn/command")[0],401)
        self.assertEqual(self.request("GET","/api/raeburn/mission?id="+"a"*32)[0],401)
        self.assertEqual(self.request("POST","/api/raeburn/create",{
            "objective":"Review operations and constraints","mode":"observe",
            "key":"testing-token-001","confirm":True})[0],401)

    def test_plan_and_mission_roundtrip(self):
        cookie=self.session()
        body={"objective":"Finish Raeburn Office production deployment",
              "mode":"autonomous"}
        status,p,_=self.request("POST","/api/raeburn/preview",body,cookie)
        self.assertEqual(status,200,p)
        self.assertTrue(p["approval_needed"])
        self.assertFalse(p["can_execute_external_action"])
        payload={**body,"key":"integration-test-001","confirm":True}
        status,created,_=self.request("POST","/api/raeburn/create",payload,cookie)
        self.assertEqual(status,200,created)
        mission=created["id"]
        status,listed,_=self.request("GET","/api/raeburn/command",cookie=cookie)
        self.assertEqual(status,200,listed)
        self.assertEqual(listed["missions"][0]["id"],mission)
        self.assertIn("audit_ok",listed)
        status,detail,_=self.request("GET","/api/raeburn/mission?id="+mission,cookie=cookie)
        self.assertEqual(status,200,detail)
        self.assertEqual(detail["mission"]["status"],"queued")
        self.assertEqual(detail["steps"][-1]["kind"],"owner.handoff")
        status,again,_=self.request("POST","/api/raeburn/create",payload,cookie)
        self.assertEqual(status,200,again)
        self.assertTrue(again["duplicate"])

    def test_mutation_requires_owner_and_csrf(self):
        cookie=self.session()
        data={"objective":"Review product project health","mode":"observe"}
        with patch.object(self.console,"role",return_value="viewer"):
            self.assertEqual(self.request("POST","/api/raeburn/preview",data,cookie)[0],403)
            self.assertEqual(self.request("GET","/api/raeburn/command",cookie=cookie)[0],403)
        with patch.object(self.console,"role",return_value="editor"):
            self.assertEqual(self.request("POST","/api/raeburn/preview",data,cookie)[0],200)
            self.assertEqual(self.request("GET","/api/raeburn/command",cookie=cookie)[0],200)
        code,_,_=self.request("POST","/api/raeburn/preview",data,cookie,
                               origin="https://other.example")
        self.assertEqual(code,403)
        self.assertEqual(cc.list_missions(),[])

    def test_missing_confirmation_and_extra_fields_rejected(self):
        cookie=self.session()
        good={"objective":"Review latest opportunities now","mode":"assist",
              "key":"requested-mission-001","confirm":True}
        for bad in ({**good,"confirm":False}, {k:v for k,v in good.items() if k!="confirm"},
                    {**good,"secret":True}):
            with self.subTest(payload=bad):
                self.assertEqual(self.request("POST","/api/raeburn/create",bad,cookie)[0],400)
        self.assertFalse(cc.list_missions())

    def test_control_status_and_invalid_ids(self):
        cookie=self.session()
        mission=cc.submit("Review the project evidence queue",mode="observe")["id"]
        for action,expected in (("pause","paused"),("resume","queued"),("cancel","cancelled")):
            code,result,_=self.request("POST","/api/raeburn/control",{
                "id":mission,"action":action,"confirm":True},cookie)
            self.assertEqual(code,200,result)
            self.assertEqual(result["status"],expected)
            self.assertFalse(result["external_action_performed"])
        self.assertEqual(self.request("GET","/api/raeburn/mission?id=../etc",cookie=cookie)[0],400)
        self.assertEqual(self.request("GET","/api/raeburn/command?unknown=true",cookie=cookie)[0],400)

    def test_ineligible_input_never_runs_shell(self):
        cookie=self.session()
        code,p,_=self.request("POST","/api/raeburn/preview",{
            "objective":"Please use password=abc123 to complete this",
            "mode":"autonomous"},cookie)
        self.assertEqual(code,400)
        self.assertFalse(cc.list_missions())
