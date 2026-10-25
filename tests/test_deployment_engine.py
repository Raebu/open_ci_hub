import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import deployment_engine as engine
from release_manager import Releases

SHA = "a"*40
CHECKS = {key:{"status":"passed","log_sha256":"b"*64}
          for key in ("tests","build","security","database","backup")}


def release_bundle(app="sampleapp",sha=SHA,checks=CHECKS):
    data={"application":app,"source_commit":sha,"reviewed_by_signer":True,"checks":checks}
    buf=io.BytesIO()
    with tarfile.open(fileobj=buf,mode="w:gz") as t:
        for name,raw in (("release.json",json.dumps(data).encode()),
                         ("app/main.py",b"print('test')\n")):
            i=tarfile.TarInfo(name);i.size=len(raw)
            t.addfile(i,io.BytesIO(raw))
    return buf.getvalue()

class DeploymentEngineSafetyTests(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root=Path(self.dir.name)
        self.deploystate=self.root/"orchestrator"
        self.releases=Releases(self.root/"releases")
        self.releases.allow("sampleapp","sampleapp.service",29999,confirm=True)
        self.releases.signers.write_text("reviewer ssh-ed25519 DEMO\n")
        self.sign=self.root/"signature"
        self.sign.write_text("stub")
        self.policy=self.root/"target.json"
        self.targets={"staging-app":{"application":"sampleapp",
                  "environment":"staging","adapter":"local-user-service","enabled":True},
              "production-app":{"application":"sampleapp","environment":"production",
                  "adapter":"local-user-service","staging_target":"staging-app",
                  "enabled":False},
              "vps-app":{"application":"sampleapp","environment":"staging",
                  "adapter":"ssh-compose","enabled":False}}
        self.save_policy()
        self.mgr=engine.DeploymentEngine(self.deploystate,self.policy,self.releases)
        self.digest=self.stage()

    def save_policy(self):
        self.policy.write_text(json.dumps({"schema":1,"targets":self.targets}))
        self.policy.chmod(0o600)

    def stage(self,data=None):
        blob=data or release_bundle()
        pkg=self.root/"bundle.tar.gz"
        pkg.write_bytes(blob)
        with patch("release_manager.subprocess.run",
                   return_value=subprocess.CompletedProcess([],0)):
            self.releases.stage("sampleapp",pkg,self.sign,
                       "reviewer",hashlib.sha256(blob).hexdigest(),confirm=True)
        return hashlib.sha256(blob).hexdigest()

    def test_private_policy_required(self):
        self.assertEqual(self.mgr.target("staging-app")["environment"],"staging")
        self.policy.chmod(0o644)
        with self.assertRaises(PermissionError):
            self.mgr.target("staging-app")

    def test_dry_run_requires_valid_signed_sha_and_gate_evidence(self):
        planned=self.mgr.plan("staging-app",SHA,self.digest)
        self.assertEqual(planned["source_commit"],SHA)
        self.assertEqual(planned["signed_evidence"]["checks"]["security"]["status"],"passed")
        self.assertFalse(planned["auto_production_enabled"])
        self.assertEqual(engine.history(self.deploystate,"staging-app"),[])
        with self.assertRaises(RuntimeError):
            self.mgr.plan("staging-app","c"*40,self.digest)

    def test_missing_security_check_fails_closed(self):
        checks={k:v for k,v in CHECKS.items() if k!="security"}
        d=self.stage(release_bundle(checks=checks))
        with self.assertRaisesRegex(RuntimeError,"security"):
            self.mgr.plan("staging-app",SHA,d)

    def test_disabled_and_remote_adapters_refused(self):
        with self.assertRaises(PermissionError):
            self.mgr.plan("production-app",SHA,self.digest)
        self.targets["vps-app"]["enabled"]=True
        self.save_policy()
        with self.assertRaises(PermissionError):
            self.mgr.plan("vps-app",SHA,self.digest)
        self.assertEqual(engine.history(self.deploystate,"vps-app"),[])
        self.targets["production-app"]["enabled"]=True
        self.save_policy()
        with self.assertRaises(NotImplementedError):
            self.mgr.plan("production-app",SHA,self.digest)

    def test_staging_needs_confirmation_and_records_only_real_health(self):
        with self.assertRaises(PermissionError):
            self.mgr.deploy("staging-app",SHA,self.digest)
        with patch.object(self.releases,"_restart"),patch.object(self.releases,"_health"):
            result=self.mgr.deploy("staging-app",SHA,self.digest,confirm=True)
        self.assertEqual(result["status"],"healthy")
        self.assertEqual(self.releases.status("sampleapp")["current"],self.digest)
        self.assertEqual([x["phase"] for x in engine.history(self.deploystate,"staging-app")],
                         ["requested","healthy"])

    def test_service_health_failure_records_failure_and_restores(self):
        with patch.object(self.releases,"_restart"),patch.object(self.releases,"_health"):
            self.mgr.deploy("staging-app",SHA,self.digest,confirm=True)
        updated=self.stage(release_bundle(sha=SHA,checks={**CHECKS,
                                         "security":{"status":"passed","log_sha256":"c"*64}}))
        with patch.object(self.releases,"_restart"),patch.object(self.releases,"_health",
                                                                   side_effect=RuntimeError("no health")):
            with self.assertRaises(RuntimeError):
                self.mgr.deploy("staging-app",SHA,updated,confirm=True)
        self.assertEqual(self.releases.status("sampleapp")["current"],self.digest)
        self.assertEqual(engine.history(self.deploystate,"staging-app")[-1]["phase"],"failed")

    def test_competing_target_lock_fails_closed(self):
        with engine.locked_target(self.deploystate,"staging-app"):
            with self.assertRaisesRegex(RuntimeError,"Another deployment"):
                with engine.locked_target(self.deploystate,"staging-app"):
                    pass

    def test_audit_log_tampering_is_detected(self):
        with patch.object(self.releases,"_restart"),patch.object(self.releases,"_health"):
            self.mgr.deploy("staging-app",SHA,self.digest,confirm=True)
        path=self.deploystate/"history"/"staging-app.jsonl"
        data=path.read_text()
        self.assertIn("healthy",data)
        path.write_text(data.replace("healthy","failed",1))
        with self.assertRaisesRegex(RuntimeError,"integrity"):
            self.mgr.status("staging-app")

    def test_production_schema_requires_matching_staging(self):
        self.targets["production-app"]["staging_target"]="vps-app"
        self.targets["vps-app"]["application"]="unrelated-app"
        self.save_policy()
        with self.assertRaises(ValueError):
            engine.load_policy(self.policy)

    def test_invalid_config_and_bypass_payload(self):
        self.targets["staging-app"]["command"]="sh -c whoami"
        self.save_policy()
        with self.assertRaises(ValueError):
            self.mgr.target("staging-app")

if __name__=="__main__":
    unittest.main()
