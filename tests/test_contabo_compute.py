"""Contabo: pure task dispatch only, production-safe isolation claims verified as argv."""
import hashlib
import json
import unittest
from unittest.mock import patch
import contabo_compute as c

class ContaboComputeTests(unittest.TestCase):
    def test_three_bounded_deterministic_operations(self):
        self.assertTrue(c.execute({"task":"json.inspect","payload":{"text":"{}"}})["valid"])
        self.assertEqual(c.execute({"task":"python.syntax","payload":{"source":"x=2"}})["valid"],True)
        self.assertEqual(c.execute({"task":"python.syntax","payload":{"source":"x="}})["valid"],False)
        self.assertEqual(c.execute({"task":"hash.benchmark","payload":{"text":"hello","iterations":1000}})["iterations"],1000)

    def test_no_arbitrary_tasks_commands_or_bad_values(self):
        bad=[
          {"task":"shell","payload":{"command":"rm -rf /"}},
          {"task":"python.syntax","payload":{"source":"x=1","command":"ls"}},
          {"task":"hash.benchmark","payload":{"text":"hi","iterations":True}},
          {"task":"hash.benchmark","payload":{"text":"hi","iterations":999999}},
          {"task":"json.inspect","payload":{"text":"A"*1501}},
          {"task":"json.inspect","payload":{"text":"{}"},"extra":True},
          {"task":"json.inspect","payload":["{}"]}
        ]
        for value in bad:
            with self.subTest(value=value),self.assertRaises(ValueError):
                c.execute(value)

    def test_remote_sandbox_is_unprivileged_offline_and_quotas(self):
        argv=c.remote_argv()
        self.assertEqual(argv[0],"ssh")
        self.assertIn("--property=DynamicUser=yes",argv)
        self.assertIn("--property=PrivateNetwork=yes",argv)
        self.assertIn("--property=NoNewPrivileges=yes",argv)
        self.assertIn("--property=ProtectSystem=strict",argv)
        self.assertIn("--property=MemoryMax=256M",argv)
        self.assertIn("--property=CPUQuota=50%",argv)
        self.assertNotIn("docker",argv)
        self.assertNotIn("sudo",argv)

    def test_verifies_remote_result_exactly(self):
        request={"task":"json.inspect","payload":{"text":"{}"}}
        correct=c.execute(request)
        result={"success":True,"result":correct}
        with patch("contabo_compute.subprocess.run") as run:
            run.return_value.returncode=0
            run.return_value.stdout=json.dumps(result).encode()
            self.assertTrue(c.dispatch(request)["verified"])
            self.assertIn(b'"json.inspect"',run.call_args.kwargs["input"])
            self.assertLessEqual(run.call_args.kwargs["timeout"],45)
            run.return_value.stdout=json.dumps({"success":True,"result":{"valid":False}}).encode()
            with self.assertRaises(RuntimeError):
                c.dispatch(request)
