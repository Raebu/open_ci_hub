"""Browser login session vault: consent, cryptography, expiry and sandbox tests."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch

import browser_session_vault as vault
from autonomy_sandbox import browser_argv,SandboxUnavailable
from autonomy_cli import browser_job

KEY=b"x"*32
SITE="example.com"
STATE={"cookies":[{"name":"sid","value":"redacted-test-cookie",
                    "domain":SITE,"path":"/","secure":True,"httpOnly":True,
                    "sameSite":"Lax","expires":1900000000}],
       "origins":[{"origin":"https://"+SITE,
                   "localStorage":[{"name":"pref","value":"dummy"}]}]}

class VaultTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir=Path(tmp.name)/"sessions"

    def test_consent_no_plaintext_immutability_and_expiry(self):
        with self.assertRaises(PermissionError):
            vault.save(SITE,STATE,key=KEY,folder=self.dir,now=1000)
        receipt=vault.save(SITE,STATE,key=KEY,folder=self.dir,
                           now=1000,ttl=3600,confirm=True)
        sealed=(self.dir/(SITE+".session.enc"))
        self.assertEqual(sealed.stat().st_mode&0o077,0)
        self.assertNotIn(b"redacted-test-cookie",sealed.read_bytes())
        self.assertEqual(vault.load(SITE,key=KEY,folder=self.dir,now=1002),STATE)
        self.assertEqual(len(vault.list_sites(folder=self.dir)),1)
        self.assertEqual(receipt["site"],SITE)
        with self.assertRaises(FileExistsError):
            vault.save(SITE,STATE,key=KEY,folder=self.dir,now=1002,confirm=True)
        with self.assertRaises(PermissionError):
            vault.load(SITE,key=KEY,folder=self.dir,now=4600)
        with self.assertRaises(PermissionError):
            vault.revoke(SITE,folder=self.dir)
        vault.revoke(SITE,folder=self.dir,confirm=True)
        self.assertFalse(sealed.exists())

    def test_wrong_site_wrong_key_and_tampered_cipher(self):
        vault.save(SITE,STATE,key=KEY,folder=self.dir,now=1000,confirm=True)
        with self.assertRaises((ValueError,PermissionError,FileNotFoundError)):
            vault.load("other.example.com",key=KEY,folder=self.dir,now=1002)
        with self.assertRaises(Exception):
            vault.load(SITE,key=b"y"*32,folder=self.dir,now=1002)
        path=self.dir/(SITE+".session.enc")
        data=json.loads(path.read_text())
        data["c"]=data["c"][:-2]+"00"
        path.write_text(json.dumps(data))
        with self.assertRaises(Exception):
            vault.load(SITE,key=KEY,folder=self.dir,now=1002)

    def test_site_scope_and_symlink(self):
        bad=json.loads(json.dumps(STATE))
        bad["cookies"][0]["domain"]=".example.com"
        with self.assertRaises(ValueError):
            vault.save(SITE,bad,key=KEY,folder=self.dir,confirm=True)
        bad=json.loads(json.dumps(STATE))
        bad["origins"][0]["origin"]="https://evil.example.com"
        with self.assertRaises(ValueError):
            vault.save(SITE,bad,key=KEY,folder=self.dir,confirm=True)
        vault.save(SITE,STATE,key=KEY,folder=self.dir,confirm=True)
        path=self.dir/(SITE+".session.enc")
        path.unlink()
        path.symlink_to("/etc/passwd")
        with self.assertRaises(PermissionError):
            vault.load(SITE,key=KEY,folder=self.dir)

    def test_local_secret_service_fail_closed(self):
        class Result:
            returncode=1
            stdout=b""
        with patch("browser_session_vault.subprocess.run",return_value=Result()):
            with self.assertRaises(PermissionError):
                vault.secret_key()
        def down(*args,**kwargs):
            raise OSError("keyring unavailable")
        with self.assertRaises(RuntimeError):
            vault.secret_key(run=down)
        with self.assertRaises(PermissionError):
            vault.secret_key(init=True,confirm=False,run=lambda *a,**k:Result())

    def test_no_credentials_in_api_metadata(self):
        vault.save(SITE,STATE,key=KEY,folder=self.dir,confirm=True)
        self.assertNotIn("redacted-test-cookie",json.dumps(vault.list_sites(folder=self.dir)))

class SandboxSessionTests(unittest.TestCase):
    def test_read_only_private_session_mount(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);app=base/"app";art=base/"art"
            app.mkdir();art.mkdir()
            (app/"browser.mjs").write_text("console.log('safe')")
            session=base/"state.json"
            session.write_text(json.dumps(STATE));session.chmod(0o600)
            argv=browser_argv(app,art,session_file=session)
            self.assertIn("/session/state.json",argv)
            self.assertIn("OCI_AUTONOMY_SESSION_STATE",argv)
            self.assertIn("--ro-bind",argv)
            session.chmod(0o644)
            with self.assertRaises(SandboxUnavailable):
                browser_argv(app,art,session_file=session)

    def test_browser_rejects_nonmatching_reused_origin(self):
        plan={"hosts":[SITE],"limits":{"max_seconds":15,
                    "allow_browser_interaction":False},
              "url":"https://"+SITE}
        step={"kind":"browse","input":{"url":"https://other.example.com",
                   "actions":[{"type":"screenshot"}]}}
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(PermissionError):
                browser_job(plan,step,Path(td)/"art",session_host=SITE)
            step["input"]["url"]="https://"+SITE
            step["input"]["actions"]=[{"type":"click","selector":"button"}]
            with self.assertRaises(PermissionError):
                browser_job(plan,step,Path(td)/"art2",session_host=SITE)
