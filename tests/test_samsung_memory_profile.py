"""Samsung Galaxy Book Go exact-device RAM floor; all other devices unchanged."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app_runtime
import fleet_devices
import portable_edge_worker as portable


class SamsungMemoryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.b=app_runtime.Broker(self.root/"state")
        self.auth={}
        for app,kind in (
            ("samsung-linux-edge","linux_arm64"),
            ("s22-edge","android_arm64"),
            ("samsung-edge","windows_arm64"),
        ):
            self.b.register(app,["json.inspect"],confirm=True)
            self.b.set_task_budget(app,"json.inspect",128,confirm=True)
            token=self.b.issue(app,"worker",confirm=True)["token"]
            self.auth[app]=self.b.auth(token)
            self.b.enroll_edge(app,kind,confirm=True)

    def report(self,app,ram=1000,charging=True):
        kind={"samsung-linux-edge":("Linux","arm64"),
              "s22-edge":("Android","arm64"),
              "samsung-edge":("Windows","arm64")}
        p,a=kind[app]
        return self.b.edge_availability(self.auth[app],{
            "platform":p,"architecture":a,"free_ram_mb":ram,
            "charging":charging})

    def test_samsung_server_896_megabytes_admits_bounded_128mb_task(self):
        self.assertFalse(self.report("samsung-linux-edge",895)["online"])
        self.assertTrue(self.report("samsung-linux-edge",896)["online"])
        self.assertTrue(self.report("samsung-linux-edge",1000)["online"])
        with self.b._db() as db:
            self.assertEqual(fleet_devices.admit(
                db,self.auth["samsung-linux-edge"],"json.inspect",128),"ready")
            self.assertNotEqual(fleet_devices.admit(
                db,self.auth["samsung-linux-edge"],"json.inspect",256),"ready")
        found={x["app"]:x for x in self.b.edge_devices()}
        self.assertTrue(found["samsung-linux-edge"]["online"])

    def test_other_devices_still_require_1536_mb(self):
        for app in ("samsung-edge","s22-edge"):
            self.assertFalse(self.report(app,1000)["online"])
            self.assertFalse(self.report(app,1535)["online"])
            self.assertTrue(self.report(app,1536)["online"])
        self.assertEqual(fleet_devices.min_free_ram_mb("s25-edge"),1536)
        self.assertEqual(fleet_devices.min_free_ram_mb("unknown-linux-edge"),1536)

    def test_samsung_requires_true_hardware_attestation_and_explicit_optin(self):
        context={"OCI_EDGE_MEMORY_PROFILE":"samsung-linux-edge"}
        with patch.dict(os.environ,context),\
             patch.object(portable.platform,"system",return_value="Linux"),\
             patch.object(portable.platform,"machine",return_value="aarch64"),\
             patch.object(portable.platform,"node",return_value="samsung-book-go"),\
             patch.object(portable.Path,"read_bytes",return_value=b"Samsung Galaxy Book Go\x00"):
            self.assertTrue(portable.samsung_book_go_memory_profile())
            self.assertEqual(portable.minimum_available_ram_mb("Linux"),896)
            self.assertEqual(portable.ready(ram=lambda:896,system="Linux"),(True,"ready"))
            self.assertEqual(portable.ready(ram=lambda:895,system="Linux"),
                             (False,"foreground_memory_reserved"))
            self.assertEqual(portable.minimum_available_ram_mb("Windows"),1536)

    def test_no_profile_or_different_model_keeps_original_floor(self):
        with patch.dict(os.environ,{"OCI_EDGE_MEMORY_PROFILE":"samsung-linux-edge"}),\
             patch.object(portable.platform,"system",return_value="Linux"),\
             patch.object(portable.platform,"machine",return_value="aarch64"),\
             patch.object(portable.platform,"node",return_value="samsung-book-go"),\
             patch.object(portable.Path,"read_bytes",return_value=b"Different laptop\x00"):
            self.assertFalse(portable.samsung_book_go_memory_profile())
            self.assertEqual(portable.ready(ram=lambda:1000,system="Linux"),
                             (False,"foreground_memory_reserved"))
        with patch.dict(os.environ,{"OCI_EDGE_MEMORY_PROFILE":""}),\
             patch.object(portable.platform,"system",return_value="Linux"),\
             patch.object(portable.platform,"machine",return_value="aarch64"),\
             patch.object(portable.platform,"node",return_value="samsung-book-go"),\
             patch.object(portable.Path,"read_bytes",return_value=b"Samsung Galaxy Book Go\x00"):
            self.assertFalse(portable.samsung_book_go_memory_profile())

    def test_existing_worker_memory_budget_never_increased(self):
        self.assertEqual(portable.MAX_RAM_MB,256)
        self.assertTrue(fleet_devices.reserved_foreground_mb("samsung-linux-edge")>=768)


if __name__=="__main__":
    unittest.main()
