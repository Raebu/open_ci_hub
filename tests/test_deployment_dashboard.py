import json
import os
import tempfile
import unittest
from pathlib import Path
from deployment_dashboard import inventory
from deployment_engine import record

class DashboardDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.state=self.root/"state"
        self.state.mkdir(mode=0o700)
        self.policy=self.root/"targets.json"
        self.policy.write_text(json.dumps({"schema":1,"targets":{
           "sample-staging":{"application":"sampleapp","environment":"staging",
                             "adapter":"cloudflare-workers","enabled":False,
                             "credential":"cloudflare-production"}
        }}))
        self.policy.chmod(0o600)
    def test_missing_policy_is_empty_not_secret_dump(self):
        got=inventory(self.root/"absent",self.state)
        self.assertFalse(got["configured"])
        self.assertEqual(got["targets"],[])
    def test_dashboard_does_not_display_credential_names_or_tokens(self):
        record(self.state,"sample-staging","requested","a"*40,"b"*64,"request")
        record(self.state,"sample-staging","healthy","a"*40,"b"*64,"ok")
        got=inventory(self.policy,self.state)
        self.assertEqual(got["targets"][0]["current_commit"],"a"*40)
        self.assertEqual(got["targets"][0]["last_status"],"healthy")
        self.assertNotIn("credential",json.dumps(got))
        self.assertNotIn("cloudflare-production",json.dumps(got))
    def test_corrupted_history_not_silently_displayed_as_healthy(self):
        record(self.state,"sample-staging","healthy","a"*40,"b"*64)
        path=self.state/"history"/"sample-staging.jsonl"
        path.write_text(path.read_text().replace("healthy","failed"))
        with self.assertRaises(RuntimeError):
            inventory(self.policy,self.state)
if __name__=="__main__": unittest.main()
