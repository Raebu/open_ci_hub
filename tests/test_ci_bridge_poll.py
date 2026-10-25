import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app_runtime import Broker
from resource_guard import ResourceGate
import ci_bridge_poll as poll

class CIOutboundPollTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.broker = Broker(Path(self.tmp.name))
        self.broker.resource_gate=ResourceGate(
            memory_reader=lambda: 16000, gpu_reader=lambda:{},
            policy_reader=lambda:{}, runner_reader=lambda:{
                "reserved_ram_mb":0, "active_runners":0, "reserved_cpus":0
            }
        )
        self.manifest={"schema":1,"repositories":{
            "Raebu/open_ci_hub":{
                "provider":"github","secret_env":"CI_TEST_WEBHOOK",
                "branches":["main"],"image":"python@sha256:"+"a"*64,
                "steps":[{"name":"smoke","argv":["python","-m","compileall","-q","."]}],
                "ram_mb":512,"max_parallel":2
            }
        }}
    def test_first_poll_baselines_without_running_or_enqueuing(self):
        with patch.object(poll,"latest_sha",return_value="a"*40) as latest:
            result=poll.poll_once(self.manifest,self.broker)
        self.assertEqual(result[0]["result"],"baseline")
        self.assertEqual(self.broker.queue_preview()["jobs"],[])
        latest.assert_called_once_with("github","Raebu/open_ci_hub","main")

    def test_change_enqueues_exact_sha_once(self):
        with patch.object(poll,"latest_sha",return_value="a"*40):
            poll.poll_once(self.manifest,self.broker)
        with patch.object(poll,"latest_sha",return_value="b"*40):
            result=poll.poll_once(self.manifest,self.broker)
            again=poll.poll_once(self.manifest,self.broker)
        self.assertEqual(result[0]["result"],"queued")
        self.assertEqual(again[0]["result"],"unchanged")
        queued=self.broker.queue_preview()["jobs"]
        self.assertEqual(len(queued),1)
        self.assertEqual(queued[0]["status"],"queued")

    def test_provider_failure_does_not_advance_or_submit(self):
        with patch.object(poll,"latest_sha",return_value="a"*40):
            poll.poll_once(self.manifest,self.broker)
        with patch.object(poll,"latest_sha",side_effect=RuntimeError("unreachable")):
            failed=poll.poll_once(self.manifest,self.broker)
        self.assertEqual(failed[0]["result"],"error")
        with patch.object(poll,"latest_sha",return_value="b"*40):
            next_result=poll.poll_once(self.manifest,self.broker)
        self.assertEqual(next_result[0]["result"],"queued")

    def test_exact_remote_ref_parsing(self):
        class Proc:
            returncode=0
            stdout=json.dumps({"commit":{"id":"c"*40}})
        with patch.object(poll.subprocess,"run",return_value=Proc()) as run:
            self.assertEqual(poll.latest_sha("gitlab","Theraeburngroup/main_ats","main"),"c"*40)
        self.assertEqual(run.call_args.args[0][0],"glab")
        self.assertIn("Theraeburngroup%2Fmain_ats",run.call_args.args[0][-1])

if __name__=="__main__": unittest.main()
