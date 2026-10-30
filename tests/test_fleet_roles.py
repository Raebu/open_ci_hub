"""Fleet power, pause visibility and safe heterogeneous scheduling."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import portable_edge_worker as worker
import fleet_connectivity as fleet
from app_runtime import Broker
from edge_pool import EdgePool

class PowerTests(unittest.TestCase):
    def sensor(self,status="Discharging",capacity="80",online=None):
        td=tempfile.TemporaryDirectory();self.addCleanup(td.cleanup)
        root=Path(td.name);battery=root/"battery";battery.mkdir()
        for key,value in {"type":"Battery","status":status,"capacity":capacity}.items():
            (battery/key).write_text(value)
        if online is not None:
            ac=root/"ac";ac.mkdir();(ac/"type").write_text("Mains");(ac/"online").write_text(online)
        return root
    def test_discharging_is_not_mains(self):
        self.assertEqual(worker.linux_power_status(self.sensor()),(False,80))
    def test_charger_and_battery_reported(self):
        self.assertEqual(worker.linux_power_status(self.sensor("Charging","79","1")),(True,79))
    def test_contradiction_fails_closed(self):
        self.assertEqual(worker.linux_power_status(self.sensor(online="1")),(False,80))
    def test_unknown_or_invalid_fails_closed(self):
        for value in ("bad","101","-1"):
            self.assertEqual(worker.linux_power_status(self.sensor(capacity=value)),(False,None))
        self.assertEqual(worker.linux_power_status(Path("/nonexistent-power-directory")),(False,None))
    def test_full_on_external_power(self):
        self.assertEqual(worker.linux_power_status(self.sensor("Full","100","1")),(True,100))
    def test_linux_unplugged_defers(self):
        self.assertEqual(worker.ready(system="Linux",ram=lambda:3000,powered=lambda:False),
                         (False,"not_connected_to_power"))
    def test_paused_worker_still_updates_presence_without_claim(self):
        client=Mock();client.availability.return_value={"enrolled":True,"online":False}
        with patch.object(worker.platform,"system",return_value="Linux"), \
             patch.object(worker.platform,"machine",return_value="aarch64"), \
             patch.object(worker,"available_ram_mb",return_value=1300), \
             patch.object(worker,"linux_power_status",return_value=(False,80)):
            result=worker.once(client,admit=lambda:(False,"not_connected_to_power"))
        client.availability.assert_called_once_with("Linux","arm64",1300,False,battery_percent=80)
        client.claim.assert_not_called()
        self.assertEqual(result["reason"],"not_connected_to_power")

class LivenessTests(unittest.TestCase):
    def test_fresh_ineligible_worker_is_paused_not_offline(self):
        now=time.time()
        data=fleet.report(devices=[{"app":"samsung-linux-edge","approved":True,
            "online":False,"last_seen":now-20}],peers={"100.77.117.120":True},clock=lambda:now)
        row=data["devices"][0]
        self.assertEqual(row["state"],"worker_paused")
        self.assertTrue(row["worker_alive"])
        self.assertFalse(row["worker_eligible"])
    def test_stale_green_cannot_claim_ready(self):
        now=time.time()
        data=fleet.report(devices=[{"app":"samsung-linux-edge","approved":True,
            "online":True,"last_seen":now-121}],peers={"100.77.117.120":True},clock=lambda:now)
        self.assertEqual(data["devices"][0]["state"],"vpn_online_worker_offline")
        self.assertFalse(data["devices"][0]["worker_online"])

class SamsungPoolTests(unittest.TestCase):
    def setUp(self):
        td=tempfile.TemporaryDirectory();self.addCleanup(td.cleanup)
        self.b=Broker(Path(td.name))
        app="samsung-linux-edge"
        self.b.register(app,["json.inspect"],confirm=True)
        self.b.set_task_budget(app,"json.inspect",128,0,confirm=True)
        self.b.set_parallel(app,1,confirm=True)
        self.auth=self.b.auth(self.b.issue(app,"worker",confirm=True)["token"])
        self.b.enroll_edge(app,"linux_arm64",confirm=True)
        self.pool=EdgePool(self.b)
    def presence(self,charging):
        self.b.edge_availability(self.auth,{"platform":"Linux","architecture":"arm64",
            "free_ram_mb":1300,"charging":charging,"battery_percent":80})
    def test_samsung_in_default_pool_and_uses_approved_memory_profile(self):
        self.presence(True)
        job=self.pool.submit("json.inspect",{"text":"{}"},request_key="samsung-profile-test",confirm=True)
        self.assertEqual(job["device"],"samsung-linux-edge")
    def test_samsung_not_scheduled_on_battery(self):
        self.presence(False)
        with self.assertRaises(RuntimeError):
            self.pool.submit("json.inspect",{"text":"{}"},request_key="samsung-battery-test",confirm=True)

if __name__=="__main__":
    unittest.main()
