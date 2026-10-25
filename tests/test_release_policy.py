"""All-ref release routing, strict security boundaries and fail-closed receipts."""
import copy
import hashlib
import unittest
import release_policy as rp


def project(**kwargs):
    data = {
        "provider": "github", "repository": "Raebu/example",
        "trusted_actors": ["Raebu"], "ci_branches": ["*"],
        "ci_tags": ["*"], "production_refs": ["refs/heads/main", "refs/tags/v*"],
        "preview_refs": ["refs/heads/feature/*", "refs/heads/release/*"],
        "deploy_target": "pc", "production_auto": True, "preview_auto": True,
        "pr_isolation_verified": False, "auto_fix": True, "max_fix_attempts": 2,
        "required_checks": ["lint", "test", "build"],
    }
    data.update(kwargs)
    return data


def event(**kwargs):
    data = {
        "provider": "github", "repository": "Raebu/example", "kind": "push",
        "ref": "refs/heads/feature/login", "sha": "a" * 40,
        "actor": "Raebu", "from_fork": False, "source_repo": "Raebu/example",
        "delivery_id": "delivery_abcd1234",
    }
    data.update(kwargs)
    return data


def policy(**kwargs):
    return {"schema": rp.SCHEMA, "projects": [project(**kwargs)]}


def passed_receipt(pl):
    return {
        "repository": pl["repository"], "sha": pl["sha"],
        "checked_out_sha": pl["sha"], "delivery_id": pl["delivery_id"],
        "status": "passed", "verified_issuer": True, "run_id": "runner-1234",
        "checks": {step: {"exit_code": 0, "observed": True,
                          "timed_out": False, "log_sha256": "c" * 64}
                   for step in pl["required_checks"]},
    }


class ReleasePolicyTests(unittest.TestCase):
    def test_all_authorised_branch_pushes_build_and_preview(self):
        for branch in ["feature/login", "fix/header", "release/1.0", "main"]:
            with self.subTest(branch=branch):
                pl = rp.plan(policy(), event(ref="refs/heads/" + branch))
                self.assertTrue(pl["run_ci"])
                self.assertEqual(pl["required_checks"], ["lint", "test", "build"])
                if branch == "main":
                    self.assertEqual(pl["deployment"], "production-pc")
                elif branch in ("feature/login", "release/1.0"):
                    self.assertEqual(pl["deployment"], "preview-pc")
                else:
                    self.assertEqual(pl["deployment"], "none")

    def test_tagged_release_goes_through_same_ci(self):
        pl = rp.plan(policy(), event(ref="refs/tags/v2.4.1"))
        self.assertEqual(pl["deployment"], "production-pc")
        self.assertTrue(pl["run_ci"])

    def test_defaults_can_disable_deployment_everywhere(self):
        pl = rp.plan(policy(production_auto=False, preview_auto=False), event())
        self.assertTrue(pl["run_ci"])
        self.assertEqual(pl["deployment"], "none")

    def test_untrusted_or_forked_push_cannot_run_persistent_pc(self):
        for change in (
            {"actor": "attacker"}, {"from_fork": True},
            {"source_repo": "attacker/example"}, {"repository": "Other/repo"},
        ):
            with self.subTest(change=change):
                pl = rp.plan(policy(), event(**change))
                self.assertFalse(pl["run_ci"])
                self.assertEqual(pl["deployment"], "none")

    def test_pr_and_mr_need_explicit_isolation_never_deploy(self):
        for provider, kind in (("github", "pull_request"), ("gitlab", "merge_request")):
            p = {"schema": rp.SCHEMA, "projects": [project(provider=provider)]}
            e = event(provider=provider, kind=kind)
            self.assertFalse(rp.plan(p, e)["run_ci"])
            p["projects"][0]["pr_isolation_verified"] = True
            admitted = rp.plan(p, e)
            self.assertTrue(admitted["run_ci"])
            self.assertEqual(admitted["deployment"], "none")
            fork = rp.plan(p, {**e, "from_fork": True})
            self.assertFalse(fork["run_ci"])

    def test_outside_scope_never_runs(self):
        p = policy(ci_branches=["main"], ci_tags=["v*"])
        self.assertFalse(rp.plan(p, event())["run_ci"])
        self.assertFalse(rp.plan(p, event(ref="refs/tags/nightly"))["run_ci"])
        self.assertTrue(rp.plan(p, event(ref="refs/heads/main"))["run_ci"])

    def test_deploy_requires_real_exact_sha_complete_checks(self):
        pl = rp.plan(policy(), event(ref="refs/heads/main"))
        receipt = passed_receipt(pl)
        self.assertTrue(rp.ready_to_deploy(pl, receipt))
        changes = [
            {"status": "superseded"}, {"status": "cancelled"},
            {"status": "skipped"}, {"status": "running"},
            {"sha": "f" * 40}, {"checked_out_sha": "f" * 40},
            {"verified_issuer": False}, {"delivery_id": "delivery_wrong"},
            {"run_id": ""},
        ]
        for change in changes:
            with self.subTest(change=change):
                self.assertFalse(rp.ready_to_deploy(pl, {**receipt, **change}))
        bad = copy.deepcopy(receipt)
        del bad["checks"]["test"]
        self.assertFalse(rp.ready_to_deploy(pl, bad))
        bad = copy.deepcopy(receipt)
        bad["checks"]["test"]["exit_code"] = 1
        self.assertFalse(rp.ready_to_deploy(pl, bad))
        bad = copy.deepcopy(receipt)
        bad["checks"]["test"]["observed"] = False
        self.assertFalse(rp.ready_to_deploy(pl, bad))
        bad = copy.deepcopy(receipt)
        bad["checks"]["test"]["log_sha256"] = ""
        self.assertFalse(rp.ready_to_deploy(pl, bad))

    def test_autofix_is_bounded_and_not_a_false_success(self):
        pl = rp.plan(policy(), event())
        self.assertEqual(pl["autofix_budget"], 2)
        self.assertEqual(pl["status"], "admitted")
        # A preview release with all required checks observed and passing is eligible.
        self.assertTrue(rp.ready_to_deploy(pl, passed_receipt(pl)))
        # An autofix budget never overrides failed CI or manufactures a green receipt.
        failed = passed_receipt(pl)
        failed["checks"]["test"]["exit_code"] = 1
        self.assertFalse(rp.ready_to_deploy(pl, failed))
        self.assertEqual(rp.plan(policy(auto_fix=False), event())["autofix_budget"], 0)
        with self.assertRaises(ValueError):
            rp.validate_config(policy(max_fix_attempts=10))

    def test_fail_closed_invalid_inputs(self):
        for e in [
            event(ref="refs/heads/../../etc/passwd"),
            event(sha="not-a-sha"), event(kind="issue"),
            event(delivery_id=""), event(actor="../malicious"),
        ]:
            with self.subTest(e=e), self.assertRaises(ValueError):
                rp.plan(policy(), e)
        with self.assertRaises(ValueError):
            rp.validate_config(policy(required_checks=[]))
        with self.assertRaises(ValueError):
            rp.validate_config(policy(production_refs=[], production_auto=True))
        with self.assertRaises(ValueError):
            rp.validate_config(policy(trusted_actors=[]))


if __name__ == "__main__":
    unittest.main()
