"""Razer fleet status separates Tailscale transport from worker liveness."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fleet_connectivity as fleet


NOW=1793267000


class FleetTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp=Path(tmp.name)
        self.file=self.tmp/"health.json"
        self.apps=[
            {"app":"samsung-linux-edge","approved":True,"online":False,
             "last_seen":NOW-28000},
            {"app":"s22-edge","approved":True,"online":False,
             "last_seen":NOW-40000},
            {"app":"s25-edge","approved":True,"online":False,
             "last_seen":NOW-14000},
        ]
        self.peers={
            "100.77.117.120":True,
            "100.95.64.110":True,
            "100.123.89.45":False,
        }

    def test_vpn_connected_does_not_claim_worker_online(self):
        health=fleet.report(devices=self.apps,peers=self.peers,clock=lambda:NOW)
        self.assertEqual(health["state"],"degraded")
        states={x["app"]:x["state"] for x in health["devices"]}
        self.assertEqual(states["samsung-linux-edge"],"vpn_online_worker_offline")
        self.assertEqual(states["s22-edge"],"vpn_online_worker_offline")
        self.assertEqual(states["s25-edge"],"device_or_vpn_offline")
        self.assertIn("worker_service",health["devices"][0]["next_step"])

    def test_all_ready_and_future_heartbeat_time_clamped(self):
        for device in self.apps:
            device["online"]=True
            device["last_seen"]=NOW+10
        self.peers["100.123.89.45"]=True
        health=fleet.report(devices=self.apps,peers=self.peers,clock=lambda:NOW)
        self.assertEqual(health["state"],"ready")
        self.assertEqual(health["devices"][0]["last_worker_seen_age_seconds"],0)

    def test_tailnet_cli_fail_closed_on_absent_or_invalid_output(self):
        class Result:
            returncode=0
            stdout="invalid-json"
        got=fleet.tailnet_online(run=lambda *args,**kwargs:Result())
        self.assertIsNone(got)
        health=fleet.report(devices=self.apps,peers={},clock=lambda:NOW)
        self.assertEqual(health["devices"][0]["state"],"vpn_state_unavailable")

    def test_same_status_does_not_mark_new_event_each_tick(self):
        first=fleet.store(fleet.report(
            devices=self.apps,peers=self.peers,clock=lambda:NOW),self.file)
        second=fleet.store(fleet.report(
            devices=self.apps,peers=self.peers,clock=lambda:NOW+60),self.file)
        self.assertTrue(first["material_change"])
        self.assertFalse(second["material_change"])
        self.assertEqual(first["state_signature_sha256"],
                         second["state_signature_sha256"])
        self.assertEqual(self.file.stat().st_mode & 0o777,0o600)
        changed={**self.peers,"100.123.89.45":True}
        third=fleet.store(fleet.report(
            devices=self.apps,peers=changed,clock=lambda:NOW+65),self.file)
        self.assertTrue(third["material_change"])

    def test_only_approved_metadata_not_secrets_persisted(self):
        health=fleet.report(devices=self.apps,peers=self.peers,clock=lambda:NOW)
        fleet.store(health,self.file)
        self.assertNotIn("token",self.file.read_text().lower())
        self.assertNotIn("ssh_key",self.file.read_text().lower())
        self.assertFalse(health["side_effects"]!="none")

    def test_status_symlink_refused_without_overwrite(self):
        target=self.tmp/"original.txt"
        target.write_text("unchanged")
        self.file.symlink_to(target)
        with self.assertRaises(ValueError):
            fleet.store(fleet.report(
                devices=self.apps,peers=self.peers,clock=lambda:NOW),self.file)
        self.assertEqual(target.read_text(),"unchanged")


if __name__=="__main__":
    unittest.main()
