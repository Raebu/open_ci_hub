"""Safety, policy and compare-and-swap regression tests for GitHub branch cleanser."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import branch_cleanser as b

A="a"*40
B="b"*40
C="c"*40
POLICY={"schema":"open-ci-hub.branch-cleanser.v1","repository":"Raebu/open_ci_hub",
        "base":"main","min_age_hours":72,"protected":["release/protected"]}

class FakeProvider:
    def __init__(self, entries=None, prs=None, merged=None):
        self.entries=entries or {"main":(A,True),"feature/old":(B,False),
                                 "feature/new":(C,False)}
        self.prs=prs or set()
        self.merged=merged or set()
        self.calls=[]
    def heads(self):
        return self.entries
    def open_pr_heads(self):
        return self.prs
    def merged_pr(self,name,sha):
        self.calls.append((name,sha))
        return name in self.merged

class PolicyTests(unittest.TestCase):
    def test_policy_rejects_symlink_missing_fields_and_weak_grace(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"policy.json"
            for raw in ('{}', '{"min_age_hours":1}', json.dumps({**POLICY,"min_age_hours":1}),
                        json.dumps({**POLICY,"repository":"evil.example/abc"}),
                        json.dumps({**POLICY,"base":"production"})):
                with self.subTest(raw=raw):
                    p.write_text(raw)
                    with self.assertRaises(b.GuardError):
                        b.read_policy(p)
            p.write_text(json.dumps(POLICY))
            self.assertEqual(b.read_policy(p)["min_age_hours"],72)
            link=Path(tmp)/"symlink.json"
            link.symlink_to(p)
            with self.assertRaises(b.GuardError):
                b.read_policy(link)

    def test_branch_ref_rejects_dangerous_git_syntax(self):
        for name in ("-bad","refs/heads/main","archive/old","a/../b",
                     "a//b","bad.lock","a@{1}","a b","", "a\nb"):
            with self.subTest(name=name),self.assertRaises(b.GuardError):
                b.validated_branch(name)
        self.assertEqual(b.validated_branch("feature/help/123"),"feature/help/123")

class SafetyTests(unittest.TestCase):
    def fixture(self):
        fake=FakeProvider()
        patcher=[
            patch.object(b,"_repo_origin_matches"),
            patch.object(b,"_sync_remote"),
            patch.object(b,"_heads_from_remote",return_value={"main":A,"feature/old":B,"feature/new":C}),
            patch.object(b,"_worktree_refs",return_value=set()),
            patch.object(b,"_head_timestamp",side_effect=lambda sha: {
                A:100,B:100,C:100_000}[sha]),
            patch.object(b,"_is_ancestor",return_value=False),
        ]
        return fake,patcher

    def test_only_old_merged_branches_eligible(self):
        fake,patchers=self.fixture()
        fake.merged={"feature/old","feature/new"}
        with patchers[0],patchers[1],patchers[2],patchers[3],patchers[4],patchers[5]:
            results=b.inspect(POLICY,now=300_000,provider=fake)
        status={r["branch"]:r["status"] for r in results}
        self.assertEqual(status["main"],"preserve")
        self.assertEqual(status["feature/old"],"eligible")
        self.assertEqual(status["feature/new"],"preserve")

    def test_worktree_open_pr_and_protected_never_deleted(self):
        fake,patchers=self.fixture()
        fake.entries["feature/old"]=(B,True)
        fake.prs={"feature/new"}
        fake.merged={"feature/old","feature/new"}
        with patchers[0],patchers[1],patchers[2],patchers[3],patchers[4],patchers[5]:
            out=b.run(POLICY,apply=False,now=600_000,provider=fake)
        self.assertEqual(out["eligible"],0)
        self.assertEqual(out["retired"],[])

    def test_unverified_provider_data_fails_closed(self):
        fake,patchers=self.fixture()
        fake.entries["feature/old"]=(A,False)
        with patchers[0],patchers[1],patchers[2],patchers[3]:
            with self.assertRaises(b.GuardError):
                b.inspect(POLICY,provider=fake)

    def test_no_deletion_on_dry_run(self):
        fake,patchers=self.fixture()
        fake.merged={"feature/old"}
        with patchers[0],patchers[1],patchers[2],patchers[3],patchers[4],patchers[5]:
            with patch.object(b,"archive_and_delete") as deletion:
                out=b.run(POLICY,apply=False,now=300_000,provider=fake)
        deletion.assert_not_called()
        self.assertEqual(out["eligible"],1)

    def test_archive_before_remote_lease_delete(self):
        commands=[]
        def git(*args,**kwargs):
            commands.append(args)
            if args[:3]==("ls-remote","origin","refs/tags/archive/cleanser/feature/old/"+B[:12]):
                return ""
            if args[:3]==("ls-remote","origin","refs/tags/archive/cleanser/feature/old/"+B[:12]+"^{}"):
                return B+"\trefs/tags/archive/cleanser/feature/old/"+B[:12]+"^{}"
            if args[:3]==("ls-remote","origin","refs/heads/feature/old"):
                return B+"\trefs/heads/feature/old"
            return ""
        with patch.object(b,"git",side_effect=git):
            result=b.archive_and_delete("feature/old",B)
        self.assertEqual(result["result"],"deleted")
        self.assertTrue(any(cmd[:2]==("tag","-a") for cmd in commands))
        self.assertTrue(any(cmd[:2]==("push","origin") and
                            cmd[2].startswith("refs/tags/") for cmd in commands))
        self.assertEqual(commands[-1][0:2],("push",f"--force-with-lease=refs/heads/feature/old:{B}"))
        self.assertEqual(commands[-1][-1],":refs/heads/feature/old")

    def test_preexisting_archive_must_match_exact_sha(self):
        tag=f"refs/tags/archive/cleanser/feature/old/{B[:12]}"
        with patch.object(b,"git",return_value=A+"\t"+tag+"^{}"):
            with self.assertRaises(b.GuardError):
                b.archive_and_delete("feature/old",B)

    def test_changed_remote_head_blocks_deletion(self):
        tag=f"refs/tags/archive/cleanser/feature/old/{B[:12]}"
        def f(*args,**kwargs):
            if args[:3]==("ls-remote","origin",tag): return B+"\t"+tag+"^{}"
            if args[:3]==("ls-remote","origin","refs/heads/feature/old"):
                return C+"\trefs/heads/feature/old"
            return ""
        with patch.object(b,"git",side_effect=f):
            with self.assertRaises(b.GuardError):
                b.archive_and_delete("feature/old",B)

    def test_missing_owner_confirmation_blocks_apply(self):
        with patch.object(b,"run") as operation:
            with patch("sys.argv",["branch_cleanser.py","--apply"]):
                self.assertEqual(b.main(),2)
            operation.assert_not_called()

if __name__=="__main__":
    unittest.main()
