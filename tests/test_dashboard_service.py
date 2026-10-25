"""No-root, no-clobber persistent private dashboard service tests."""
from pathlib import Path
from unittest.mock import patch
import tempfile
import unittest

from scripts import dashboard_service as svc

ROOT=Path(__file__).resolve().parents[1]

class DashboardServiceTests(unittest.TestCase):
    def test_unit_is_private_user_only_and_bounded(self):
        source=svc.unit_text(ROOT)
        for line in ("Type=exec","ExecStart=/usr/bin/python3 -u hub.py dashboard --no-browser",
                "NoNewPrivileges=true","PrivateTmp=true","Restart=on-failure",
                "MemoryMax=768M","CPUQuota=60%","WantedBy=default.target"):
            self.assertIn(line,source)
        self.assertNotIn("0.0.0.0",source)
        self.assertNotIn("Tailscale Funnel",source)
        self.assertNotIn("ExecStart=/bin/sh",source)

    def test_no_owner_confirmation_refuses(self):
        with self.assertRaises(PermissionError):
            svc.install(root=ROOT,confirmed=False)

    def test_reject_existing_conflicting_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            unit=Path(tmp)/".config/systemd/user"/svc.NAME
            unit.parent.mkdir(parents=True)
            unit.write_text("[Unit]\nDescription=unrelated application\n")
            with patch.object(svc.os,"getuid",return_value=1000),patch.object(
                    svc,"_run",side_effect=["main","","ignored"]):
                with self.assertRaises(RuntimeError):
                    svc.install(root=ROOT,home=tmp,confirmed=True)
            self.assertIn("unrelated application",unit.read_text())

    def test_matching_service_is_idempotent_and_health_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            unit=Path(tmp)/".config/systemd/user"/svc.NAME
            unit.parent.mkdir(parents=True)
            unit.write_text(svc.unit_text(ROOT))
            calls=[]
            def call(*args):
                calls.append(args)
                if args[:2]==("git","-C") and args[-2:]==("branch","--show-current"):
                    return "main"
                if args[:2]==("git","-C"):
                    return ""
                if args[:3]==("systemctl","--user","is-active"):
                    return "active"
                return ""
            class Response:
                status=200
                def read(self,n): return b'{"ok": true}'
                def __enter__(self):return self
                def __exit__(self,*args):return False
            with patch.object(svc.os,"getuid",return_value=1000),patch.object(
                    svc,"_run",side_effect=call),patch.object(
                    svc,"urlopen",return_value=Response()):
                result=svc.install(root=ROOT,home=tmp,confirmed=True)
            self.assertEqual(result["action"],"already_installed")
            self.assertEqual(result["health"],"ok")
            self.assertEqual(unit.read_text(),svc.unit_text(ROOT))
            self.assertTrue(any(x[:3]==("systemctl","--user","enable") for x in calls))
