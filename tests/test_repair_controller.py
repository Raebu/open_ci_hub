"""Regression tests for bounded patch validation and exact-evidence merge decisions."""
import copy
import unittest

import repair_controller as rc


PATCH = """diff --git a/src/example.py b/src/example.py
index 1111111..2222222 100644
--- a/src/example.py
+++ b/src/example.py
@@ -1 +1 @@
-old = True
+new = True
"""


def evidence(**changes):
    sha = "a" * 40
    data = {
        "schema": rc.SCHEMA,
        "repository": "Raebu/example",
        "base_sha": "b" * 40,
        "candidate_sha": sha,
        "pr_number": 42,
        "trusted_actor": True,
        "risk": "low",
        "required_checks": ["unit", "lint"],
        "checks": {
            name: {"candidate_sha": sha, "conclusion": "success",
                   "observed": True, "log_sha256": "c" * 64,
                   "issuer_verified": True}
            for name in ("unit", "lint")
        },
        "review": {"approved": True, "reviewer_trusted": True,
                   "reviewer_is_author": False, "candidate_sha": sha,
                   "pr_number": 42, "repository": "Raebu/example"},
        "branch_protection": {"enabled": True, "required_checks_enforced": True,
                              "review_enforced": True, "admin_bypass": False},
        "merge_state": "clean",
        "source_isolated": True,
        "patch_paths": ["src/example.py"],
    }
    data.update(changes)
    return data


class PatchValidationTests(unittest.TestCase):
    def test_valid_patch_is_hash_bound_and_never_merges(self):
        result = rc.validate_patch(PATCH, allow_patterns=["src/**"])
        self.assertTrue(result["valid"])
        self.assertEqual(result["files"], ["src/example.py"])
        self.assertEqual(len(result["patch_sha256"]), 64)
        self.assertFalse(result["automatic_merge_eligible"])

    def test_rejects_protected_or_unallowlisted_paths(self):
        for path in (".github/workflows/test.yml", "auth_broker.py",
                     "src/../secret.py", "secrets/token.txt", "src/thing.key"):
            patch = PATCH.replace("src/example.py", path)
            with self.subTest(path=path), self.assertRaises(rc.GateError):
                rc.validate_patch(patch, allow_patterns=["**"])

    def test_rejects_deletion_rename_symlink_and_binary(self):
        cases = [
            PATCH + "\nnew file mode 120000\n",
            PATCH + "\nrename from src/example.py\nrename to src/other.py\n",
            PATCH.replace("@@ -1 +1 @@", "@@ -1 +0 @@") +
                "\n--- /dev/null\n",
            "GIT binary patch\n",
        ]
        for patch in cases:
            with self.subTest(patch=patch[:60]), self.assertRaises(rc.GateError):
                rc.validate_patch(patch, allow_patterns=["**"])

    def test_rejects_malformed_or_oversized_patch(self):
        for patch in ("", "not a diff", PATCH + "\x00", "x" * (rc.MAX_PATCH_BYTES + 1)):
            with self.subTest(size=len(patch)), self.assertRaises((rc.GateError, UnicodeError)):
                rc.validate_patch(patch, allow_patterns=["**"])


class MergeDecisionTests(unittest.TestCase):
    def test_all_exact_evidence_can_become_eligible_but_is_not_merged(self):
        result = rc.merge_decision(evidence())
        self.assertTrue(result["eligible"])
        self.assertFalse(result["merge_performed"])
        self.assertEqual(result["candidate_sha"], "a" * 40)

    def test_every_missing_or_unsafe_gate_denies(self):
        changes = [
            {"trusted_actor": False},
            {"risk": "medium"},
            {"source_isolated": False},
            {"merge_state": "dirty"},
            {"branch_protection": {"enabled": True, "required_checks_enforced": True,
                                   "review_enforced": True, "admin_bypass": True}},
            {"review": {"approved": True, "reviewer_trusted": True,
                        "reviewer_is_author": True, "candidate_sha": "a" * 40,
                        "pr_number": 42, "repository": "Raebu/example"}},
            {"patch_paths": [".github/workflows/test.yml"]},
            {"candidate_sha": "d" * 40},
            {"checks": {}},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(rc.GateError):
                rc.merge_decision({**evidence(), **change})

    def test_check_must_match_exact_candidate_and_verified_issuer(self):
        for change in (
            {"candidate_sha": "d" * 40},
            {"conclusion": "failure"},
            {"observed": False},
            {"issuer_verified": False},
            {"log_sha256": "bad"},
        ):
            data = copy.deepcopy(evidence())
            data["checks"]["unit"].update(change)
            with self.subTest(change=change), self.assertRaises(rc.GateError):
                rc.merge_decision(data)

    def test_missing_unknown_fields_and_invalid_pr_denied(self):
        data = evidence()
        del data["review"]
        with self.assertRaises(rc.GateError):
            rc.merge_decision(data)
        data = evidence(pr_number=0)
        with self.assertRaises(rc.GateError):
            rc.merge_decision(data)


if __name__ == "__main__":
    unittest.main()
