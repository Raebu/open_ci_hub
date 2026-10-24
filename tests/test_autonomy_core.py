"""Deterministic security tests for the human-first autonomy subsystem."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from autonomy_core import (
    Journal, PolicyError, admit_local, choose_worker, public_url, validate_plan,
)
from autonomy_cli import execute_fixed, run_one


def sample_plan(*, interactive=False):
    return {
        "id": "mission-alpha", "hosts": ["example.com"],
        "limits": {"max_cost_usd": 0, "max_seconds": 25,
                   "allow_browser_interaction": interactive},
        "steps": [
            {"id": "step-one", "kind": "digest", "needs": [],
             "input": {"text": "hello"}},
            {"id": "step-two", "kind": "inspect", "needs": ["step-one"],
             "input": {"text": '{"a":2,"b":1}'}},
            {"id": "step-three", "kind": "browse", "needs": ["step-two"],
             "input": {"url": "https://example.com/",
                       "actions": [{"type": "extract", "selector": "h1"}]}}
        ]
    }


class MissionPolicyTests(unittest.TestCase):
    def test_basic_plan(self):
        self.assertEqual(validate_plan(sample_plan())["id"], "mission-alpha")

    def test_reject_non_public_or_unsafe_url(self):
        for url in ("http://example.com", "https://localhost",
                    "https://127.0.0.1", "https://example.com.evil.org",
                    "file:///etc/passwd", "https://user:password@example.com",
                    "https://example.com:4444", "https://example.com/#frag"):
            with self.subTest(url=url), self.assertRaises(PolicyError):
                public_url(url, frozenset({"example.com"}))

    def test_reject_external_action_or_invalid_schema(self):
        plan = sample_plan()
        plan["steps"][2]["input"]["actions"] = [
            {"type": "javascript", "selector": "body", "value": "process.exit()"}]
        with self.assertRaises(PolicyError):
            validate_plan(plan)

    def test_refuse_interaction_without_scope(self):
        p = sample_plan()
        p["steps"][2]["input"]["actions"] = [
            {"type": "click", "selector": "button"}]
        with self.assertRaises(PolicyError):
            validate_plan(p)
        p["limits"]["allow_browser_interaction"] = True
        validate_plan(p)

    def test_dag_and_duplicate_ids(self):
        p = sample_plan()
        p["steps"][1]["needs"] = ["step-three"]
        with self.assertRaises(PolicyError):
            validate_plan(p)
        p = sample_plan()
        p["steps"][1]["id"] = "step-one"
        with self.assertRaises(PolicyError):
            validate_plan(p)

    def test_cost_ceiling_explicit(self):
        p = sample_plan()
        p["limits"]["max_cost_usd"] = -1
        with self.assertRaises(PolicyError):
            validate_plan(p)

    def test_host_gate_preserves_foreground(self):
        self.assertEqual(admit_local({"mem_available_mb": 7000,
                                     "load_per_core": .2, "max_thermal_c": 60})[0], True)
        for metrics in ({"mem_available_mb": 2000, "load_per_core": .1},
                        {"mem_available_mb": 7000, "load_per_core": 1.2},
                        {"mem_available_mb": 7000, "load_per_core": .1,
                         "max_thermal_c": 90},
                        {"mem_available_mb": None, "load_per_core": .1}):
            with self.subTest(metrics=metrics):
                self.assertFalse(admit_local(metrics)[0])

    def test_fleet_cost_architecture_and_permissions(self):
        workers = [
            {"id": "phone", "online": True, "approved": True,
             "capabilities": ["digest"], "architecture": "arm64",
             "free_ram_mb": 5000, "energy_rank": 1, "cost_usd": 0},
            {"id": "cloud", "online": True, "approved": True,
             "capabilities": ["digest"], "architecture": "arm64",
             "free_ram_mb": 10000, "energy_rank": 0, "cost_usd": None},
            {"id": "unsafe", "online": True, "approved": False,
             "capabilities": ["digest"], "architecture": "arm64",
             "free_ram_mb": 30000, "energy_rank": 0, "cost_usd": 0},
        ]
        self.assertEqual(choose_worker(workers, {"architecture": "arm64",
                       "ram_mb": 512, "kind": "digest"})["id"], "phone")
        self.assertIsNone(choose_worker(workers, {"architecture": "x86_64",
                                "ram_mb": 512, "kind": "digest"}))


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.journal = Journal(Path(self.dir.name) / "private")

    def test_owner_approval_and_duplicate_prevention(self):
        with self.assertRaises(PermissionError):
            self.journal.submit(sample_plan())
        self.journal.submit(sample_plan(), owner_approved=True)
        with self.assertRaises(Exception):
            self.journal.submit(sample_plan(), owner_approved=True)

    def test_durable_dependency_sequence_and_evidence(self):
        self.journal.submit(sample_plan(), owner_approved=True)
        ok = {"mem_available_mb": 8000, "load_per_core": .1, "max_thermal_c": 40}
        run_one(self.journal, "mission-alpha", sensor=lambda: ok)
        r = run_one(self.journal, "mission-alpha", sensor=lambda: ok)
        self.assertEqual(r["step"], "step-two")
        r = run_one(self.journal, "mission-alpha", sensor=lambda: ok,
                    browser=lambda plan, step, artifacts, timeout:
                        {"ok": True, "final_url": step["input"]["url"],
                         "records": []})
        self.assertEqual(r["status"], "completed")
        for step in self.journal.snapshot("mission-alpha")["steps"]:
            self.assertEqual(step["status"], "completed")
            self.assertEqual(len(step["evidence_sha256"]), 64)

    def test_running_job_is_not_replayed_after_restart(self):
        self.journal.submit(sample_plan(), owner_approved=True)
        self.journal.start_step("mission-alpha", "step-one")
        self.assertEqual(self.journal.interrupted("mission-alpha"), ["step-one"])
        result = run_one(self.journal, "mission-alpha", sensor=lambda: {})
        self.assertEqual(result["status"], "blocked")

    def test_defer_when_user_machine_busy(self):
        self.journal.submit(sample_plan(), owner_approved=True)
        out = run_one(self.journal, "mission-alpha",
                      sensor=lambda: {"mem_available_mb": 2500, "load_per_core": 1.4})
        self.assertEqual(out["status"], "deferred")
        self.assertEqual(self.journal.ready_step("mission-alpha")["id"], "step-one")

    def test_cancellation_requires_quiescent_job(self):
        self.journal.submit(sample_plan(), owner_approved=True)
        with self.assertRaises(PermissionError):
            self.journal.cancel("mission-alpha")
        self.journal.cancel("mission-alpha", owner_approved=True)
        self.assertIsNone(self.journal.ready_step("mission-alpha"))

    def test_fixed_operations_do_not_execute_code(self):
        data = {"id": "foo", "kind": "digest", "input": {"text": "rm -rf ~"}}
        self.assertTrue(execute_fixed(data)["ok"])
        self.assertFalse(execute_fixed({"kind": "inspect",
                         "input": {"text": "import os"}})["valid"])


if __name__ == "__main__":
    unittest.main()
