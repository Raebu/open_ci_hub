"""Runtime, API-first safety and owner status regression tests."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomy_core import Journal, PolicyError, validate_plan
from autonomy_cli import RefuseRedirect, fetch_readonly
from autonomy_daemon import candidates, tick
from autonomy_dashboard import summary


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.journal = Journal(self.folder / "state")
        self.metrics = lambda: {
            "mem_available_mb": 8000, "load_per_core": .1, "max_thermal_c": 45
        }

    def test_empty_status_no_personal_data(self):
        answer = summary(self.folder / "nothing.sqlite", metrics=self.metrics)
        self.assertFalse(answer["configured"])
        self.assertEqual(answer["missions"], [])
        self.assertTrue(answer["admission"]["eligible"])

    def test_dashboard_summary_contains_no_plan_or_payload(self):
        p = {"id":"only-demo", "hosts":[],
             "limits":{"max_cost_usd":0,"max_seconds":15,
                       "allow_browser_interaction":False},
             "steps":[{"id":"one","kind":"digest","needs":[],
                       "input":{"text":"private value must not appear"}}]}
        self.journal.submit(p, owner_approved=True)
        answer = summary(self.journal.path, metrics=self.metrics)
        self.assertEqual(answer["missions"][0]["id"], "only-demo")
        self.assertNotIn("private value", str(answer))
        self.assertFalse(any("plan" in row for row in answer["missions"]))

    def test_daemon_will_not_authorise_work(self):
        self.assertEqual(tick(self.journal)["status"], "idle")
        p = {"id":"only-demo", "hosts":[],
             "limits":{"max_cost_usd":0,"max_seconds":15,
                       "allow_browser_interaction":False},
             "steps":[{"id":"one","kind":"digest","needs":[],
                       "input":{"text":"safe"}}]}
        self.journal.submit(p, owner_approved=True)
        self.assertEqual(candidates(self.journal), ["only-demo"])
        out = tick(self.journal, runner=lambda journal, mission:
                   {"status":"completed","step":"one"})
        self.assertEqual(out["step"], "one")

    def test_api_fetch_schema_rejects_private_host(self):
        p = {"id":"only-demo", "hosts":["example.com"],
             "limits":{"max_cost_usd":0,"max_seconds":15,
                       "allow_browser_interaction":False},
             "steps":[{"id":"one","kind":"fetch","needs":[],
                       "input":{"url":"https://localhost/secret"}}]}
        with self.assertRaises(PolicyError):
            validate_plan(p)

    def test_api_fetch_rejects_private_dns_addresses(self):
        fake = [(None,None,None,None,("127.0.0.1",443))]
        with patch("autonomy_cli.socket.getaddrinfo",return_value=fake):
            response = fetch_readonly("https://example.com/", ["example.com"])
        self.assertEqual(response["error"], "nonpublic_network_address")

    def test_redirect_never_followed(self):
        self.assertIsNone(RefuseRedirect().redirect_request(
            None,None,302,"redirect",{},"https://evil.example/"))


if __name__ == "__main__":
    unittest.main()
