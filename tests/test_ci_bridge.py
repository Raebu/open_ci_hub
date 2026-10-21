import hashlib, hmac, json, os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import ci_bridge as bridge

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.state=Path(self.tmp.name)/"state"
        self.manifest={
            "schema":1,
            "repositories":{"Raebu/open_ci_hub":{
                "provider":"github","secret_env":"TEST_HOOK_SECRET","branches":["main"],
                "image":"python:3.12-slim@sha256:"+"a"*64,
                "steps":[{"name":"unit-tests","argv":["python","-m","unittest","discover","-s","tests"]}],
                "max_seconds":120
            }}
        }
        self.path=self.state/"manifest.json"
        self.state.mkdir(mode=0o700)
        self.path.write_text(json.dumps(self.manifest))
        self.path.chmod(0o600)
        self.addCleanup(self.tmp.cleanup)
        self.old_state=bridge.STATE
        self.old_manifest=bridge.MANIFEST
        bridge.STATE=self.state
        bridge.MANIFEST=self.path
        self.addCleanup(setattr,bridge,"STATE",self.old_state)
        self.addCleanup(setattr,bridge,"MANIFEST",self.old_manifest)

    def test_manifest_requires_private_file_and_digest_pinned_image(self):
        self.assertEqual(bridge.load_manifest(self.path)["schema"],1)
        self.path.chmod(0o644)
        with self.assertRaises(PermissionError): bridge.load_manifest(self.path)
        self.path.chmod(0o600)
        bad=dict(self.manifest);bad["repositories"]=dict(self.manifest["repositories"])
        bad["repositories"]["Raebu/open_ci_hub"]=dict(bad["repositories"]["Raebu/open_ci_hub"],image="python:latest")
        self.path.write_text(json.dumps(bad))
        with self.assertRaises(ValueError): bridge.load_manifest(self.path)

    def test_github_webhook_signature_is_constant_time_verified(self):
        body=b'{"after":"' + b"a"*40 + b'"}'
        secret="x"*40
        signature="sha256="+hmac.new(secret.encode(),body,hashlib.sha256).hexdigest()
        bridge.verify_signature("github",secret,body,{"X-Hub-Signature-256":signature})
        with self.assertRaises(PermissionError):
            bridge.verify_signature("github",secret,body,{"X-Hub-Signature-256":"sha256="+"0"*64})

    def test_parse_github_push_pins_exact_sha_and_branch(self):
        sha="b"*40
        headers={"X-GitHub-Event":"push"}
        body=json.dumps({"repository":{"full_name":"Raebu/open_ci_hub"},"ref":"refs/heads/main","after":sha}).encode()
        self.assertEqual(bridge.parse_event("github",headers,body),("Raebu/open_ci_hub",sha,"main","push"))

    def test_queue_is_idempotent_and_branch_allowlisted(self):
        sha="c"*40
        first=bridge.enqueue("github","Raebu/open_ci_hub",sha,"main","push",self.manifest)
        again=bridge.enqueue("github","Raebu/open_ci_hub",sha,"main","push",self.manifest)
        self.assertEqual(first["job_id"],again["job_id"])
        self.assertTrue(again["duplicate"])
        with self.assertRaises(PermissionError):
            bridge.enqueue("github","Raebu/open_ci_hub",sha,"feature","push",self.manifest)
        self.assertEqual(bridge.status()[0]["sha"],sha)

    def test_unallowlisted_repo_and_bad_sha_are_rejected(self):
        with self.assertRaises(PermissionError):
            bridge.enqueue("github","someone/else","d"*40,"main","push",self.manifest)
        with self.assertRaises(ValueError):
            bridge.enqueue("github","Raebu/open_ci_hub","not-a-sha","main","push",self.manifest)

if __name__=="__main__": unittest.main()
