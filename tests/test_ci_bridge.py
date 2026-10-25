import hashlib, hmac, json, tempfile, unittest
from pathlib import Path
from resource_guard import ResourceGate
from app_runtime import Broker
import ci_bridge as bridge

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.state=Path(self.tmp.name)/"state"
        self.state.mkdir(mode=0o700)
        self.path=self.state/"manifest.json"
        self.manifest={
            "schema":1,
            "repositories":{
                "Raebu/open_ci_hub":{
                    "provider":"github","secret_env":"TEST_HOOK_SECRET","branches":["main"],
                    "image":"python:3.12-slim@sha256:"+"a"*64,
                    "steps":[{"name":"unit-tests","argv":["python","-m","unittest","discover","-s","tests"]}],
                    "max_seconds":120,"ram_mb":1024,"max_parallel":2
                },
                "Raebu/another-project":{
                    "provider":"github","secret_env":"TEST_HOOK_SECRET_2","branches":["main"],
                    "image":"python:3.12-slim@sha256:"+"b"*64,
                    "steps":[{"name":"unit-tests","argv":["python","-m","unittest","discover","-s","tests"]}],
                    "max_seconds":120,"ram_mb":1024,"max_parallel":2
                },
                "Raebu/third-project":{
                    "provider":"github","secret_env":"TEST_HOOK_SECRET_3","branches":["main"],
                    "image":"python:3.12-slim@sha256:"+"c"*64,
                    "steps":[{"name":"unit-tests","argv":["python","-m","unittest","discover","-s","tests"]}],
                    "max_seconds":120,"ram_mb":1024,"max_parallel":2
                }
            }
        }
        self.path.write_text(json.dumps(self.manifest))
        self.path.chmod(0o600)
        self.broker=Broker(self.state/"broker")
        self.broker.resource_gate=ResourceGate(memory_reader=lambda:16000,gpu_reader=lambda:{},
            policy_reader=lambda:{},runner_reader=lambda:{"reserved_ram_mb":0,"active_runners":0,"reserved_cpus":0})
        self.addCleanup(self.tmp.cleanup)

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

    def test_pull_request_branch_policy_uses_target_branch(self):
        sha="b"*40
        body=json.dumps({"repository":{"full_name":"Raebu/open_ci_hub"},
          "action":"opened","pull_request":{"draft":False,
          "head":{"sha":sha,"ref":"feature","repo":{"full_name":"Raebu/open_ci_hub"}},
          "base":{"ref":"main"}}}).encode()
        self.assertEqual(bridge.parse_event("github",{"X-GitHub-Event":"pull_request"},body),
                         ("Raebu/open_ci_hub",sha,"main","pull_request"))

    def test_merge_request_branch_policy_uses_target_branch(self):
        sha="c"*40
        body=json.dumps({"project":{"id":123,"path_with_namespace":"Raebu/open_ci_hub"},
          "object_attributes":{"action":"open","state":"opened","target_project_id":123,
          "source_project_id":123,"source_branch":"feature","target_branch":"main",
          "last_commit":{"id":sha}}}).encode()
        self.assertEqual(bridge.parse_event("gitlab",{"X-Gitlab-Event":"Merge Request Hook"},body),
                         ("Raebu/open_ci_hub",sha,"main","merge_request"))

    def test_fork_pull_requests_are_rejected_by_default(self):
        sha="b"*40
        body=json.dumps({"repository":{"full_name":"Raebu/open_ci_hub"},
          "action":"opened","pull_request":{"draft":False,"head":{"sha":sha,"ref":"feature",
          "repo":{"full_name":"someone/open_ci_hub"}}}}).encode()
        with self.assertRaises(PermissionError):
            bridge.parse_event("github",{"X-GitHub-Event":"pull_request"},body)

    def test_queue_is_idempotent_and_branch_allowlisted(self):
        bridge.ensure_scheduler(self.manifest,self.broker)
        sha="c"*40
        first=bridge.enqueue("github","Raebu/open_ci_hub",sha,"main","push",self.manifest,self.broker)
        again=bridge.enqueue("github","Raebu/open_ci_hub",sha,"main","push",self.manifest,self.broker)
        self.assertEqual(first["job_id"],again["job_id"])
        self.assertTrue(again["duplicate"])
        with self.assertRaises(PermissionError):
            bridge.enqueue("github","Raebu/open_ci_hub",sha,"feature","push",self.manifest,self.broker)
        self.assertEqual(bridge.status(self.broker)[0]["sha"],sha)

    def test_unallowlisted_repo_and_bad_sha_are_rejected(self):
        bridge.ensure_scheduler(self.manifest,self.broker)
        with self.assertRaises(PermissionError):
            bridge.enqueue("github","someone/else","d"*40,"main","push",self.manifest,self.broker)
        with self.assertRaises(ValueError):
            bridge.enqueue("github","Raebu/open_ci_hub","not-a-sha","main","push",self.manifest,self.broker)

    def test_shared_scheduler_allows_parallel_projects_up_to_global_limit(self):
        bridge.ensure_scheduler(self.manifest,self.broker)
        self.broker.set_global_resources(2,2048,0,confirm=True)
        bridge.enqueue("github","Raebu/open_ci_hub","1"*40,"main","push",self.manifest,self.broker)
        bridge.enqueue("github","Raebu/another-project","2"*40,"main","push",self.manifest,self.broker)
        bridge.enqueue("github","Raebu/third-project","3"*40,"main","push",self.manifest,self.broker)
        one,reason=bridge.claim_one(self.manifest,self.broker)
        two,reason2=bridge.claim_one(self.manifest,self.broker)
        three,reason3=bridge.claim_one(self.manifest,self.broker)
        self.assertIsNotNone(one)
        self.assertIsNotNone(two)
        self.assertNotEqual(one["job_id"],two["job_id"])
        self.assertIsNone(three)
        self.assertEqual(reason3,"global_parallel_limit")
        self.assertEqual(self.broker.resource_status()["active_jobs"],2)

if __name__=="__main__": unittest.main()
