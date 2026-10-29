"""S25 20% unplugged operation with server-side per-device approval.

No live Android device, secrets or real customer workload is touched.
"""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock,patch
import os

import app_runtime
import application_client
import edge_worker as worker
import fleet_devices
from edge_pool import EdgePool


class S25PowerPolicyTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.b=app_runtime.Broker(Path(tmp.name)/"hub")
        self.workers={}
        for app in ("s25-edge","s22-edge"):
            self.b.register(app,["json.inspect"],confirm=True)
            self.b.set_task_budget(app,"json.inspect",128,confirm=True)
            self.workers[app]=self.b.auth(self.b.issue(app,"worker",confirm=True)["token"])
            self.b.enroll_edge(app,"android_arm64",confirm=True)

    def sensor(self,percent=57,plugged="UNPLUGGED"):
        return {"percentage":percent,"temperature":30.5,"health":"GOOD","plugged":plugged}

    def wifi(self):
        return {"ip":"192.168.0.32","supplicant_state":"COMPLETED"}

    def presence(self,app,battery=57,charging=False,free_ram_mb=3200):
        return self.b.edge_availability(self.workers[app],{
            "platform":"Android","architecture":"arm64",
            "free_ram_mb":free_ram_mb,"charging":charging,
            "battery_percent":battery})

    def test_phone_57_percent_unplugged_is_eligible_only_with_s25_profile(self):
        self.assertEqual(worker.eligible(self.sensor(),self.wifi()),
                         (False,"not_charging"))
        self.assertEqual(worker.eligible(
            self.sensor(),self.wifi(),power_profile="s25-edge"),
            (True,"eligible"))
        self.assertEqual(worker.eligible(
            self.sensor(),self.wifi(),power_profile="unapproved-device"),
            (False,"unapproved_power_profile"))

    def test_20_percent_reserve_and_temperature_and_wlan_hold(self):
        for pct in (21,22,40,57,99):
            with self.subTest(percent=pct):
                self.assertTrue(worker.eligible(
                    self.sensor(pct),self.wifi(),power_profile="s25-edge")[0])
        for pct in (0,19,20):
            with self.subTest(percent=pct):
                self.assertEqual(worker.eligible(
                    self.sensor(pct),self.wifi(),power_profile="s25-edge"),
                    (False,"battery_low"))
        for battery,wifi in (
            ({**self.sensor(),"temperature":40},self.wifi()),
            ({**self.sensor(),"health":"OVERHEAT"},self.wifi()),
            (self.sensor(),{"ip":"0.0.0.0","supplicant_state":"DISCONNECTED"}),
            ({**self.sensor(),"percentage":True},self.wifi()),
            ({**self.sensor(),"plugged":"UNKNOWN"},self.wifi()),
        ):
            self.assertFalse(worker.eligible(
                battery,wifi,power_profile="s25-edge")[0])

    def test_server_accepts_s25_unplugged_but_denies_s22(self):
        self.assertTrue(self.presence("s25-edge")["online"])
        rows={x["app"]:x for x in self.b.edge_devices()}
        self.assertTrue(rows["s25-edge"]["online"])
        self.assertEqual(rows["s25-edge"]["battery_percent"],57)
        self.assertFalse(self.presence("s22-edge")["online"])
        self.assertFalse({x["app"]:x for x in self.b.edge_devices()}["s22-edge"]["online"])
        self.assertFalse(self.presence("s25-edge",battery=20)["online"])
        self.assertFalse(self.presence("s25-edge",battery=19)["online"])
        self.assertTrue(self.presence("s25-edge",battery=21)["online"])
        self.assertTrue(self.presence("s22-edge",battery=75,charging=True)["online"])

    def test_server_rejects_telemetry_forgery_and_missing_percentage(self):
        base={"platform":"Android","architecture":"arm64",
              "free_ram_mb":3200,"charging":False}
        self.assertFalse(self.b.edge_availability(self.workers["s25-edge"],base)["online"])
        for invalid in (None,False,101,-1,55.5,"57"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    self.b.edge_availability(
                        self.workers["s25-edge"],{**base,"battery_percent":invalid})
        with self.assertRaises(ValueError):
            self.b.edge_availability(self.workers["s25-edge"],{
                **base,"battery_percent":57,"secret":"unapproved"})
        with self.assertRaises(PermissionError):
            submit=self.b.auth(self.b.issue("s25-edge","submit",confirm=True)["token"])
            self.b.edge_availability(submit,{**base,"battery_percent":57})

    def test_broker_claim_and_dispatch_works_only_above_reserve(self):
        p=EdgePool(self.b)
        # S22 unplugged; S25 57% unplugged. Only S25 is a candidate.
        self.presence("s22-edge",battery=57)
        self.presence("s25-edge",battery=57)
        result=p.submit("json.inspect",{"text":"{}"},request_key="s25-unplugged-test-001",confirm=True)
        self.assertEqual(result["device"],"s25-edge")
        self.presence("s25-edge",battery=20)
        with self.assertRaises(RuntimeError):
            p.submit("json.inspect",{"text":"{}"},request_key="s25-low-reserve-002",confirm=True)

    def test_old_worker_reports_remain_compatible_and_charging_required_for_s22(self):
        default={"platform":"Android","architecture":"arm64",
                 "free_ram_mb":3200,"charging":True}
        self.assertTrue(self.b.edge_availability(self.workers["s22-edge"],default)["online"])
        listing={x["app"]:x for x in self.b.edge_devices()}
        self.assertIsNone(listing["s22-edge"]["battery_percent"])
        default["charging"]=False
        self.assertFalse(self.b.edge_availability(self.workers["s22-edge"],default)["online"])

    def test_backwards_compatible_client_payload_and_optional_percent(self):
        client=object.__new__(application_client.ApplicationClient)
        client._request=Mock(return_value={"enrolled":True})
        client.availability("Android","arm64",3500,True)
        body=client._request.call_args.args[2]
        self.assertNotIn("battery_percent",body)
        client.availability("Android","arm64",3500,False,57)
        body=client._request.call_args.args[2]
        self.assertIs(body["charging"],False)
        self.assertEqual(body["battery_percent"],57)
        with self.assertRaises(ValueError):
            client.availability("Android","arm64",3500,False,"57")
        with self.assertRaises(ValueError):
            client.availability("Android","arm64",3500,False,True)

    def test_s25_once_reports_real_unplugged_flag_not_claim_charging(self):
        client=Mock()
        client.availability.return_value={"enrolled":True,"online":True}
        client.claim.return_value={"job":None}
        sample=self.sensor()
        with patch.dict(os.environ,{"OCI_EDGE_POWER_PROFILE":"s25-edge"}),\
             patch.object(worker,"termux_sensor",side_effect=lambda name:
                          sample if "battery" in name else self.wifi()),\
             patch.object(worker.Path,"read_text",return_value="MemAvailable: 6000000 kB"):
            status=worker.once(client)
        self.assertEqual(status["status"],"idle")
        client.availability.assert_called_once_with(
            "Android","arm64",6000000//1024,False,57)

    def test_new_db_migration_old_fleet_schema(self):
        # A real pre-change data file must retain its credentials/enrolments.
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path=Path(temp.name)/"state"
        path.mkdir()
        dbpath=path/"application-runtime.sqlite"
        with sqlite3.connect(dbpath) as db:
            db.execute("""CREATE TABLE fleet_devices (
                app TEXT PRIMARY KEY,credential_hash TEXT UNIQUE NOT NULL,
                kind TEXT,allowed_tasks TEXT,approved INTEGER,
                free_ram_mb INTEGER,charging INTEGER,last_seen REAL,created REAL)""")
        app_runtime.Broker(path)
        with sqlite3.connect(dbpath) as db:
            cols={x[1] for x in db.execute("PRAGMA table_info(fleet_devices)")}
        self.assertIn("battery_percent",cols)


if __name__=="__main__":
    unittest.main()
