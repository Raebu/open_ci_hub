"""GitHub CI evidence intake, repository review gate and private brief tests."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import business_execution as be
import business_briefing
from tests.test_business_execution import config


class GitHubReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/"test-source/.git").mkdir(parents=True)
        self.patch=patch.object(be,"PROJECTS",self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.calls=[]
        def command(argv,**kwargs):
            self.calls.append(argv)
            if argv[0]=="git":
                return "a"*40 if "rev-parse" in argv else ""
            if argv[:3]==["gh","run","list"]:
                return json.dumps([
                    {"databaseId":456,"headSha":"a"*40,"status":"completed",
                     "conclusion":"success","createdAt":"2026-10-28T05:00:00Z",
                     "workflowName":"CI"}])
            if argv[:3]==["gh","pr","list"]:
                return json.dumps([
                    {"number":12,"url":"https://github.com/Raebu/test-source/pull/12",
                     "headRefName":"fix","baseRefName":"main","isDraft":False},
                    {"number":99,"url":"https://malicious.example/pr/99",
                     "headRefName":"evil","baseRefName":"main","isDraft":False}
                ])
            return None
        self.command=command
        self.bridge=be.Bridge(state=self.root/"state",config=config(),command=command)

    def test_github_status_is_not_release_permission(self):
        found=self.bridge.ci_snapshots()
        self.assertEqual(found[0]["ci"],"github_ci_reported_pass_release_gates_pending")
        with self.bridge.db() as db:
            rows=db.execute("SELECT kind,stage FROM work").fetchall()
        self.assertTrue(any(r["kind"]=="pull_request"
                            and r["stage"]=="open_pr_requires_trusted_checks_and_review"
                            for r in rows))
        self.assertEqual(sum(x["kind"]=="pull_request" for x in rows),1)
        self.assertFalse(self.bridge.readiness()["lanes"][2]["cutover_allowed"])
        self.assertFalse(any("merge" in " ".join(argv) for argv in self.calls))

    def test_no_matching_sha_does_not_claim_success(self):
        def mismatch(argv,**kw):
            if argv[0]=="gh" and argv[1]=="run":
                return json.dumps([{"databaseId":1,"headSha":"f"*40,
                                    "status":"completed","conclusion":"success"}])
            return self.command(argv,**kw)
        bridge=be.Bridge(state=self.root/"mismatch",config=config(),command=mismatch)
        self.assertEqual(bridge.ci_snapshots()[0]["ci"],
                         "ci_for_current_checkout_unverified")

    def test_private_brief_untrusted_issue_title_not_executed_or_exposed(self):
        self.bridge.observe("product","repository","Raebu/test-source",
                            "release_gate_required",{"commit":"a"*40})
        self.bridge.observe("product","github_issue","Raebu/test-source#23",
                            "issue_needs_evidence",
                            {"url":"https://github.com/Raebu/test-source/issues/23"})
        self.bridge.ci_snapshots()
        file=self.root/"state"/"briefing.md"
        result=business_briefing.save(self.bridge,file)
        self.assertEqual(file.stat().st_mode&0o777,0o600)
        body=file.read_text()
        self.assertIn("not merged",body)
        self.assertIn("release gates pending",body.replace("_"," "))
        self.assertIn("https://github.com/Raebu/test-source/issues/23",body)
        self.assertNotIn("secret@company.example",body)
        self.assertEqual(result["executed_external_actions"],0)
        file.unlink()
        file.symlink_to(self.root/"test-source")
        with self.assertRaises(PermissionError):
            business_briefing.save(self.bridge,file)


if __name__=="__main__":
    unittest.main()
