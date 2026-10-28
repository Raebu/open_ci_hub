"""Connector capability handoff: numeric-only, ephemeral and never OAuth proof."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

import business_connector_bridge as b
from business_channels import ChannelError


def sample():
    return {"schema":b.SCHEMA,
            "observed_at":datetime.now(timezone.utc).isoformat(),
            "entries":[
                {"alias":"raeburn-primary","provider":"gmail",
                 "state":"connected_read_probe","bounded_result_count":12,
                 "more_available":True},
                {"alias":"raeburn-contact","provider":"gmail",
                 "state":"connected_read_probe","bounded_result_count":12,
                 "more_available":True},
                {"alias":"raeburn-resend","provider":"resend",
                 "state":"connected_read_probe","bounded_result_count":12,
                 "more_available":True},
                {"alias":"linkedin-profile-only","provider":"linkedin",
                 "state":"profile_lookup_only","bounded_result_count":2,
                 "more_available":False}
            ]}


class ConnectorProbeTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp=Path(tmp.name)
        self.output=self.tmp/"probes.json"

    def test_probe_is_privacy_bounded_and_not_local_oauth(self):
        report=b.store(sample(),self.output)
        self.assertEqual(report["recorded"],4)
        status=b.load(self.output)
        self.assertEqual(status["state"],"connected_app_probe_fresh")
        self.assertFalse(status["native_api_credentials_verified"])
        self.assertFalse(status["independent_continuous_sync"])
        self.assertEqual(self.output.stat().st_mode&0o777,0o600)
        self.assertNotIn("subject",self.output.read_text())

    def test_stale_does_not_claim_connection(self):
        x=sample()
        x["observed_at"]=(datetime.now(timezone.utc)-timedelta(
            hours=3)).isoformat()
        with self.assertRaises(ChannelError):
            b.store(x,self.output)
        self.assertEqual(b.load(self.output)["state"],"not_recorded")

    def test_reject_personal_accounts_and_free_text(self):
        bad=sample()
        bad["entries"][0]["email"]="family@example.org"
        with self.assertRaises(ChannelError):
            b.validate(bad)
        bad=sample()
        bad["entries"][1]["alias"]="personal-outlook"
        with self.assertRaises(ChannelError):
            b.validate(bad)
        bad=sample()
        bad["entries"][3]["state"]="connected_read_probe"
        with self.assertRaises(ChannelError):
            b.validate(bad)

    def test_symlink_destination_never_overwrites_target(self):
        target=self.tmp/"original"
        target.write_text("untouched")
        self.output.symlink_to(target)
        with self.assertRaises(ChannelError):
            b.store(sample(),self.output)
        self.assertEqual(target.read_text(),"untouched")


if __name__=="__main__":
    unittest.main()
