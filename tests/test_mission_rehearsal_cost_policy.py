"""Tests for non-executing policy, zero-cost device routing, session consent."""
import unittest
from copy import deepcopy
from mission_rehearsal import rehearse
from session_cache_policy import approve_site_session
from zero_cost_dispatch import WorkerOffer,choose
from autonomy_core import PolicyError

class RehearsalTests(unittest.TestCase):
    def plan(self):
        return {"id":"review-browser","hosts":["example.com"],
         "limits":{"max_cost_usd":0,"max_seconds":30,"allow_browser_interaction":False},
         "steps":[{"id":"capture","kind":"browse","needs":[],"input":{
            "url":"https://example.com/","actions":[{"type":"screenshot"}]}}]}
    def test_rehearsal_never_runs(self):
        result=rehearse(self.plan())
        self.assertEqual(result["executed"],False)
        self.assertEqual(result["steps"][0]["execution"],"not_started")
        self.assertEqual(len(result["plan_sha256"]),64)
    def test_unsafe_task_rejected(self):
        p=self.plan()
        p["steps"][0]["input"]["actions"]=[{"type":"click","selector":"button"}]
        with self.assertRaises(PolicyError):
            rehearse(p)

class EdgeBrokerTests(unittest.TestCase):
    def offer(self,**kw):
        defaults={"name":"samsung","owner_approved":True,"online":True,
            "capabilities":frozenset({"json.inspect"}),"free_ram_mb":3000,
            "required_ram_mb":128,"monetary_cap_pence":0,"watts":8,
            "latency_ms":50,"device_kind":"laptop"}
        defaults.update(kw)
        return WorkerOffer(**defaults)
    def test_free_owned_worker_selected(self):
        result=choose([self.offer(),self.offer(name="garuda",watts=65)],
                      {"json.inspect"})
        self.assertEqual(result["selected"],"samsung")
    def test_unknown_cloud_cost_fails_closed(self):
        self.assertIsNone(choose([self.offer(name="cloud",monetary_cap_pence=None,
                       device_kind="cloud")],{"json.inspect"})["selected"])
        self.assertIsNone(choose([self.offer(monetary_cap_pence=1)],
                       {"json.inspect"})["selected"])
        self.assertIsNone(choose([self.offer(online=False)],{"json.inspect"})["selected"])
    def test_battery_critical(self):
        self.assertIsNone(choose([self.offer()],{"json.inspect"},
                      battery_critical=True)["selected"])
    def test_missing_capability(self):
        self.assertIsNone(choose([self.offer()],{"python.syntax"})["selected"])

class SessionPolicyTests(unittest.TestCase):
    def test_reuse_requires_owner_and_is_read_only(self):
        with self.assertRaises(PermissionError):
            approve_site_session("example.com",owner_confirmed=False,ttl_hours=24)
        with self.assertRaises(PermissionError):
            approve_site_session("example.com",owner_confirmed=True,
                                 ttl_hours=24,allow_interaction=True)
        with self.assertRaises(ValueError):
            approve_site_session("example.com",owner_confirmed=True,ttl_hours=10000)
        self.assertEqual(approve_site_session("example.com",owner_confirmed=True,
                         ttl_hours=24)["secret_storage"],"encrypted_vault_opt_in")
