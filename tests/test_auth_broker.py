"""Owner authentication coordinator: safe status, approvals and encrypted rotation."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import os
import unittest

import auth_broker as broker
import browser_session_vault as vault
import browser_session_grants as grants

HOST="example.com"
KEY=b"Z"*32
STATE={"cookies":[{"name":"test","value":"never-log-this-dummy",
                   "domain":HOST,"secure":True}],
       "origins":[{"origin":"https://"+HOST,"localStorage":[]}]}

class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder=Path(temp.name)/"vault"
        vault.save(HOST,STATE,key=KEY,folder=self.folder,
                   now=1000,ttl=86400,confirm=True)

    def test_owner_scoped_status_does_not_expose_sessions(self):
        with patch.object(broker,"enabled",return_value=False):
            record=broker.overview(folder=self.folder)
        self.assertEqual(record["host_count"],1)
        self.assertEqual(record["sites"][0]["site"],HOST)
        self.assertNotIn("never-log-this-dummy",json.dumps(record))
        self.assertFalse(record["raw_credentials_accessible_to_agents"])

    def test_import_must_be_private_and_confirmed(self):
        source=self.folder.parent/"new.json"
        source.write_text(json.dumps(STATE))
        source.chmod(0o600)
        with self.assertRaises(PermissionError):
            broker.import_state(HOST,source,folder=self.folder)
        with patch.object(vault,"secret_key",return_value=KEY):
            broker.import_state(HOST,source,folder=self.folder,confirm=True)
            outcome=vault.load(HOST,key=KEY,folder=self.folder)
        self.assertEqual(outcome,STATE)
        self.assertNotIn("never-log-this-dummy",
          (self.folder/(HOST+".session.enc")).read_text())

    def test_bad_replacement_preserves_previous_ciphertext(self):
        original=(self.folder/(HOST+".session.enc")).read_bytes()
        source=self.folder.parent/"malformed.json"
        source.write_text('{"cookies":[{"domain":"other.com"}],"origins":[]}')
        source.chmod(0o600)
        with patch.object(vault,"secret_key",return_value=KEY):
            with self.assertRaises(ValueError):
                broker.import_state(HOST,source,folder=self.folder,confirm=True)
        self.assertEqual(original,(self.folder/(HOST+".session.enc")).read_bytes())

    def test_matching_approval_no_secret_output(self):
        grants.authorise(HOST,folder=self.folder,key=KEY,now=1000,confirm=True)
        with (patch.object(grants,"secret_key",return_value=KEY),
              patch.object(vault,"secret_key",return_value=KEY)):
            with patch.object(broker,"enabled",
                              side_effect=lambda *a,**kw: grants.enabled(*a,key=KEY,**kw)):
                info=broker.check(HOST,folder=self.folder,now=1001)
        self.assertTrue(info["available"])
        self.assertEqual(info["scope"],"read_only_exact_https_host")
        self.assertNotIn("never-log-this-dummy",str(info))
        other=broker.check("other.com",folder=self.folder,now=1001)
        self.assertFalse(other["available"])

    def test_explicit_rotation_only_no_symlinks(self):
        source=self.folder.parent/"new.json"
        source.write_text(json.dumps(STATE));source.chmod(0o600)
        path=self.folder/(HOST+".session.enc")
        path.unlink()
        path.symlink_to("/etc/passwd")
        with patch.object(vault,"secret_key",return_value=KEY):
            with self.assertRaises(PermissionError):
                broker.import_state(HOST,source,folder=self.folder,confirm=True)
