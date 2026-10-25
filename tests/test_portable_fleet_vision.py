"""Cross-platform work tasks and private vision regressions; no external devices."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from portable_edge_worker import task,execute,ready,once
from visual_observer import screenshot_from_step,observe
from autonomy_core import validate_plan,PolicyError

class PortableTests(unittest.TestCase):
    def test_fixed_operations(self):
        self.assertTrue(task("python.syntax",{"source":"a=1"})["valid"])
        self.assertFalse(task("python.syntax",{"source":"("})["valid"])
        self.assertTrue(task("json.inspect",{"text":'{"x":1}'})["valid"])
        self.assertEqual(task("hash.benchmark",{"text":"a","iterations":1000})["iterations"],1000)
        with self.assertRaises(ValueError):
            task("arbitrary.shell",{"cmd":"whoami"})
        with self.assertRaises(ValueError):
            task("hash.benchmark",{"text":"x","iterations":999999})

    def test_real_child_isolation_and_no_code_execution(self):
        ok,reply=execute("python.syntax",{"source":"import os\nos.system('touch /tmp/NEVER_EXECUTE_OPEN_CI')"})
        self.assertTrue(ok,reply)
        self.assertTrue(reply["valid"])
        self.assertFalse(Path("/tmp/NEVER_EXECUTE_OPEN_CI").exists())
        self.assertFalse(execute("shell",{"cmd":"whoami"})[0])

    def test_windows_resource_admission(self):
        self.assertEqual(ready(ram=lambda:1024,system="Windows",powered=lambda:True)[0],False)
        self.assertEqual(ready(ram=lambda:2000,system="Windows",powered=lambda:False)[1],"not_connected_to_power")
        self.assertEqual(ready(ram=lambda:2000,system="Windows",powered=lambda:True)[0],True)

    def test_private_broker_claim_and_completion(self):
        client=Mock()
        client.claim.return_value={"job":{"job_id":"a"*32,"lease_token":"private",
              "task":"json.inspect","payload":{"text":"{}"},
              "resources":{"ram_mb":128,"vram_mb":0}}}
        client.complete.return_value={"status":"completed"}
        client.availability.return_value={"enrolled":True,"online":True}
        with patch("portable_edge_worker.execute",return_value=(True,{"valid":True})) as op:
            state=once(client,admit=lambda:(True,"ready"))
        self.assertEqual(state["status"],"acknowledged")
        op.assert_called_once()
        client.complete.assert_called_once()
        client.claim.return_value={"job":{"job_id":"b"*32,"lease_token":"private",
              "task":"json.inspect","payload":{"text":"{}"},
              "resources":{"ram_mb":1024,"vram_mb":0}}}
        result=once(client,admit=lambda:(True,"ready"))
        self.assertFalse(result["success"])
        self.assertEqual(result["hub_status"],"completed") # mock response, not claim

class VisionTests(unittest.TestCase):
    def test_read_private_screenshot_only(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"page-0.jpg"
            p.write_bytes(b"\xff\xd8\xff"+b"0"*40)
            data=screenshot_from_step(td,{"ok":True,"records":[{
                "action":"screenshot","filename":"page-0.jpg"}]})
            self.assertEqual(data,p.read_bytes())
            with self.assertRaises(ValueError):
                screenshot_from_step(td,{"ok":True,"records":[{
                    "action":"screenshot","filename":"../../secrets.jpg"}]})
            p.unlink()
            p.symlink_to("/etc/passwd")
            with self.assertRaises(ValueError):
                screenshot_from_step(td,{"ok":True,"records":[{
                    "action":"screenshot","filename":"page-0.jpg"}]})

    def test_visual_http_fixed_loopback_and_untrusted_output(self):
        response=Mock(status=200)
        response.read.return_value=json.dumps({"message":{"content":"A button labelled Next."}}).encode()
        conn=Mock()
        conn.getresponse.return_value=response
        seen=[]
        def make(host,port,timeout):
            seen.append((host,port,timeout))
            return conn
        answer=observe(b"\xff\xd8\xff"+b"0"*40,"What is visible on this page?",connection=make)
        self.assertTrue(answer["ok"])
        self.assertEqual(seen[0][:2],("127.0.0.1",11434))
        self.assertEqual(answer["source"],"local_only_untrusted_observation")
        body=json.loads(conn.request.call_args.kwargs["body"])
        self.assertEqual(body["stream"],False)
        self.assertEqual(body["model"],"qwen3.5:0.8b")

    def test_vision_requires_explicit_screenshot_dependency(self):
        plan={"id":"vision-test","hosts":["example.com"],"limits":{
            "max_cost_usd":0,"max_seconds":30,"allow_browser_interaction":False},
            "steps":[{"id":"capture","kind":"browse","needs":[],"input":{
                "url":"https://example.com/","actions":[{"type":"screenshot"}]}},
                {"id":"review","kind":"vision","needs":["capture"],"input":{
                    "source_step":"capture","question":"What is visible on this screenshot?"}}]}
        validate_plan(plan)
        plan["steps"][1]["needs"]=[]
        with self.assertRaises(PolicyError):
            validate_plan(plan)

if __name__=="__main__":
    unittest.main()
