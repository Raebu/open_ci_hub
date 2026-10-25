"""Android edge safety and work dispatch regressions, without device or network."""
import unittest
from unittest.mock import Mock, patch
import edge_worker as e

class EdgeTests(unittest.TestCase):
    battery = {"percentage": 85, "temperature": 31.2, "health": "GOOD",
               "plugged": "PLUGGED_USB"}
    wifi = {"ip": "192.168.1.10", "supplicant_state": "COMPLETED"}

    def test_good_phone(self):
        self.assertEqual(e.eligible(self.battery, self.wifi), (True, "eligible"))

    def test_missing_and_invalid_sensors_block(self):
        for b, w in ((None, self.wifi), (self.battery, None), ({}, self.wifi),
                     ({**self.battery, "temperature": "bad"}, self.wifi),
                     ({**self.battery, "temperature": float("nan")}, self.wifi),
                     ({**self.battery, "temperature": 40}, self.wifi),
                     ({**self.battery, "percentage": 69}, self.wifi),
                     ({**self.battery, "plugged": "UNPLUGGED"}, self.wifi),
                     ({**self.battery, "health": "OVERHEAT"}, self.wifi),
                     (self.battery, {**self.wifi, "ip": "0.0.0.0"}),
                     (self.battery, {**self.wifi, "supplicant_state": "DISCONNECTED"})):
            with self.subTest(b=b, w=w):
                self.assertFalse(e.eligible(b, w)[0])

    def test_sensor_name_allowlist(self):
        with self.assertRaises(ValueError):
            e.termux_sensor("bash")

    def test_python_parse_is_not_execution(self):
        self.assertTrue(e.fixed_task("python.syntax",
                        {"source": "__import__('os').system('exit 1')"})["valid"])
        self.assertFalse(e.fixed_task("python.syntax", {"source": "def x("})["valid"])

    def test_json_canonical_digest(self):
        a = e.fixed_task("json.inspect", {"text": '{"x":1,"y":2}'})
        b = e.fixed_task("json.inspect", {"text": '{"y":2,"x":1}'})
        self.assertEqual(a["sha256"], b["sha256"])

    def test_benchmark_bounded(self):
        self.assertEqual(len(e.fixed_task("hash.benchmark",
                             {"text": "x", "iterations": 1000})["sha256"]), 64)
        for count in (0, 50001, True, "1000"):
            with self.assertRaises(ValueError):
                e.fixed_task("hash.benchmark", {"text": "x", "iterations": count})

    def test_reject_unknown_oversize(self):
        with self.assertRaises(ValueError):
            e.fixed_task("os.exec", {})
        with self.assertRaises(ValueError):
            e.fixed_task("python.syntax", {"source": "a" * 1501})

    def test_real_isolated_child(self):
        success, result = e.execute("python.syntax", {"source": "x = 1"})
        self.assertTrue(success, result)
        self.assertTrue(result["valid"])

    def test_no_token_sent_to_child(self):
        with patch.object(e.subprocess, "run") as run:
            run.return_value = Mock(returncode=0,
                                    stdout=b'{"success":true,"result":{}}')
            self.assertTrue(e.execute("json.inspect", {"text": "{}"})[0])
            self.assertNotIn("OCI_EDGE_WORKER_TOKEN", run.call_args.kwargs["env"])
            self.assertIn("-I", run.call_args.args[0])

    def test_child_limit_matches_reservation(self):
        with patch.object(e.subprocess, "run") as run:
            run.return_value = Mock(returncode=0,
                                    stdout=b'{"success":true,"result":{}}')
            self.assertTrue(e.execute("json.inspect", {"text": "{}"}, ram_mb=128)[0])
            self.assertEqual(run.call_args.kwargs["preexec_fn"].args, (128,))
            self.assertEqual(e.execute("json.inspect", {"text": "{}"}, ram_mb=1024),
                             (False, {"error": "unsupported_resource_profile"}))

    def test_unapproved_task_does_not_spawn(self):
        self.assertEqual(e.execute("bash", {}), (False, {"error": "task_not_approved"}))

    @patch.object(e, "phone_ready", return_value=(False, "battery_low"))
    def test_skips_without_claim(self, ready):
        client = Mock()
        self.assertEqual(e.once(client)["status"], "skipped")
        client.claim.assert_not_called()

    @patch.object(e, "phone_ready", return_value=(True, "eligible"))
    @patch.object(e, "execute", return_value=(True, {"valid": True}))
    def test_claim_and_completion(self, execute, ready):
        client = Mock()
        client.claim.return_value = {"job": {
            "job_id": "a" * 32, "lease_token": "private",
            "task": "python.syntax", "payload": {"source": "x=1"},
            "resources": {"ram_mb": 512, "vram_mb": 0}}}
        client.complete.return_value = {"status": "completed"}
        client.availability.return_value = {"enrolled": True, "online": True}
        self.assertEqual(e.once(client)["hub_status"], "completed")
        client.complete.assert_called_once_with("a" * 32, "private", True, {"valid": True})

    @patch.object(e, "execute")
    def test_reject_excess_memory_or_gpu(self, execute):
        client = Mock()
        for budget in ({"ram_mb": 1024, "vram_mb": 0},
                       {"ram_mb": 512, "vram_mb": 1}):
            e.complete_claim(client, {"job_id": "a" * 32, "lease_token": "private",
                          "task": "python.syntax", "payload": {},
                          "resources": budget})
        execute.assert_not_called()
        self.assertEqual(client.complete.call_count, 2)

    @patch.object(e, "phone_ready", return_value=(False, "battery_warm"))
    @patch.object(e, "execute")
    def test_temperature_change_after_claim(self, execute, ready):
        client = Mock()
        e.complete_claim(client, {"job_id": "a" * 32, "lease_token": "private",
                   "task": "python.syntax", "payload": {},
                   "resources": {"ram_mb": 512, "vram_mb": 0}})
        execute.assert_not_called()
        self.assertFalse(client.complete.call_args.args[2])

if __name__ == "__main__":
    unittest.main()
