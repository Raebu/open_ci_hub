"""Owner dashboard / mobile editor mission controls must remain authenticated."""
import tempfile
from pathlib import Path
from unittest.mock import patch

from tests.test_web_console import DashboardHTTPTests
from autonomy_core import Journal


class AutonomyHTTPTests(DashboardHTTPTests):
    def setUp(self):
        self.temp_state = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_state.cleanup)
        self.journal = Journal(Path(self.temp_state.name)/"autonomy")
        self.journal.submit({
            "id":"test-mission", "hosts": [],
            "limits":{"max_cost_usd":0,"max_seconds":10,
                      "allow_browser_interaction":False},
            "steps":[{"id":"step-a","kind":"digest","needs":[],
                      "input":{"text":"private test data"}}]
        },owner_approved=True)

    def test_login_required_for_control(self):
        code,_,_=self.request("POST","/api/autonomy/control",{
            "mission":"test-mission","action":"pause","confirm":True})
        self.assertEqual(code,401)
        code,_,_=self.request("GET","/api/autonomy")
        self.assertEqual(code,401)

    def test_owner_can_pause_resume_cancel_without_releasing_running_work(self):
        session = self.session()
        with patch("autonomy_cli.DEFAULT_STATE", self.journal.folder):
            with patch("autonomy_dashboard.STATE", self.journal.path):
                code,data,_=self.request("GET","/api/autonomy",cookie=session)
                self.assertEqual(code,200)
                self.assertEqual(data["missions"][0]["id"],"test-mission")
                self.assertNotIn("private test data",str(data))
                for action,status in (("pause","paused"),("resume","queued"),("cancel","cancelled")):
                    code,data,_=self.request("POST","/api/autonomy/control",{
                        "mission":"test-mission","action":action,"confirm":True},cookie=session)
                    self.assertEqual(code,200,data)
                    self.assertEqual(self.journal.snapshot("test-mission")["status"],status)

    def test_malformed_controls_not_authorised(self):
        cookie=self.session()
        with patch("autonomy_cli.DEFAULT_STATE", self.journal.folder):
            for value in (
                {"mission":"test-mission","action":"pause"},
                {"mission":"../../etc/passwd","action":"pause","confirm":True},
                {"mission":"test-mission","action":"shell","confirm":True},
                {"mission":"test-mission","action":"pause","confirm":True,"unknown":"x"}
            ):
                with self.subTest(value=value):
                    code,_,_=self.request("POST","/api/autonomy/control",value,cookie)
                    self.assertEqual(code,400)
            self.assertEqual(self.journal.snapshot("test-mission")["status"],"queued")

    def test_foreign_origin_refused(self):
        cookie=self.session()
        with patch("autonomy_cli.DEFAULT_STATE", self.journal.folder):
            code,_,_=self.request("POST","/api/autonomy/control",{
                "mission":"test-mission","action":"pause","confirm":True},
                cookie,origin="https://attacker.example")
            self.assertEqual(code,403)
            self.assertEqual(self.journal.snapshot("test-mission")["status"],"queued")
