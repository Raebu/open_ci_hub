"""Provider adapters must not contact real deployments during tests."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deployment_providers import CredentialStore, CloudflareVersions, VercelAlias, SSHCompose, https_json, https_health


class AdapterTests(unittest.TestCase):
    def setUp(self):
        # Protocol tests run behind a mocked admission boundary.
        gate=patch("cost_policy.deny_unverified_external_work")
        gate.start(); self.addCleanup(gate.stop)
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.root.chmod(0o700)
        for name,value in (("cloudflare","cf-token"),("vercel","vercel-token"),("ssh","ssh-dummy"),
                           ("hosts","ssh-ed25519 dummy")):
            p=self.root/name;p.write_text(value);p.chmod(0o600)
        self.store=CredentialStore(self.root)
    def test_credential_permission_and_name(self):
        self.assertEqual(self.store.token("cloudflare"),"cf-token")
        (self.root/"cloudflare").chmod(0o644)
        with self.assertRaises(PermissionError):
            self.store.token("cloudflare")
        with self.assertRaises(ValueError):
            self.store.file("../cloudflare")
    def test_cloudflare_immutable_version_and_rollback(self):
        events=[]
        old="12345678-1234-1234-1234-123456789012"
        new="23456789-1234-1234-1234-123456789012"
        def request(method,url,*,token,payload=None):
            events.append((method,url,payload,token))
            if method=="GET":
                return {"success":True,"result":{"deployments":[{"versions":[{"version_id":old,"percentage":100}]}]}}
            return {"success":True,"result":{"id":"34567890-1234-1234-1234-123456789012"}}
        a=CloudflareVersions({"account_id":"a"*32,"script":"test-worker","credential":"cloudflare"},
                              self.store,request)
        self.assertEqual(a.current(),old)
        self.assertEqual(a.promote(new)["version"],new)
        a.rollback(old)
        self.assertTrue(all(x[3]=="cf-token" for x in events))
        self.assertEqual(events[1][2]["versions"][0]["percentage"],100)
        with self.assertRaises(ValueError):
            a.promote("../foo")
    def test_split_traffic_is_not_automatically_promoted(self):
        old="12345678-1234-1234-1234-123456789012"
        request=lambda method,url,*,token,payload=None:{"success":True,"result":{"deployments":[{"versions":[{"version_id":old,"percentage":50}]}]}}
        adapter=CloudflareVersions({"account_id":"a"*32,"script":"test-worker","credential":"cloudflare"},self.store,request)
        with self.assertRaises(RuntimeError):
            adapter.current()
    def test_vercel_ready_immutable_prebuilt_version(self):
        calls=[]
        def request(method,url,*,token,payload=None):
            calls.append((method,url,token,payload))
            if "/v4/aliases/" in url:
                return {"deployment":{"id":"dpl_originaldeployment1"}}
            if "/v13/deployments/" in url:
                return {"readyState":"READY"}
            return {"uid":"alias123"}
        a=VercelAlias({"alias":"preview.example.org","credential":"vercel"},self.store,request)
        self.assertEqual(a.current(),"dpl_originaldeployment1")
        result=a.promote("dpl_newdeployment1")
        self.assertEqual(result["alias"],"preview.example.org")
        self.assertTrue(all(c[2]=="vercel-token" for c in calls))
        self.assertTrue(calls[-1][1].startswith("https://api.vercel.com/v2/deployments/"))
    def test_unready_vercel_version_rejected(self):
        request=lambda method,url,*,token,payload=None:{"state":"BUILDING"}
        a=VercelAlias({"alias":"preview.example.org","credential":"vercel"},self.store,request)
        with self.assertRaises(RuntimeError):
            a.promote("dpl_newdeployment1")
    def test_ssh_commands_are_fixed_and_keys_pinned(self):
        called=[]
        def runner(argv,**kwargs):
            called.append(argv)
            return subprocess.CompletedProcess(argv,0,stdout="example/image@sha256:"+"a"*64+"\n")
        cfg={"host":"staging.example.org","user":"deploy","project":"ats","compose":"/srv/ats/release.yaml","credential":"ssh","known_hosts":"hosts"}
        a=SSHCompose(cfg,self.store,runner)
        self.assertEqual(len(a.execute("images")["images"]),1)
        self.assertIn("StrictHostKeyChecking=yes",called[0])
        with self.assertRaises(ValueError):
            a.execute("rm")
        with self.assertRaises(ValueError):
            SSHCompose({**cfg,"host":"-oProxyCommand=evil"},self.store,runner)
    def test_unexpected_provider_host_rejected(self):
        with self.assertRaises(ValueError):
            https_json("GET","http://api.cloudflare.com/",token="not-used")
        with self.assertRaises(ValueError):
            https_health("https://malicious.example.org/health",allowed_host="approved.example.org")

if __name__=="__main__": unittest.main()
