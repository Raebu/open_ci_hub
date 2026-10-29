"""Verify the production worker preserves protections and trusted SSH transport."""
from pathlib import Path
import stat
import unittest

ROOT=Path(__file__).resolve().parents[1]
UNIT=ROOT/"linux-worker"/"raeburn-command-worker.service"
SHIM=ROOT/"linux-worker"/"raeburn-ssh-bin"/"ssh"
TARGET=ROOT/"linux-worker"/"raeburn-ssh-shim.sh"

class WorkerDeploymentPolicyTests(unittest.TestCase):
    def test_ssh_wrapper_only_bypasses_unsafe_global_file(self):
        self.assertTrue(SHIM.is_symlink())
        self.assertEqual(SHIM.resolve(),TARGET.resolve())
        self.assertTrue(TARGET.is_file())
        self.assertTrue(TARGET.stat().st_mode & stat.S_IXUSR)
        text=TARGET.read_text()
        self.assertIn("exec /usr/bin/ssh -F /dev/null",text)
        self.assertNotIn("StrictHostKeyChecking=no",text)

    def test_worker_retains_privilege_and_resource_guards(self):
        unit=UNIT.read_text()
        for required in (
            "MemoryMax=192M","CPUQuota=45%","TasksMax=48",
            "NoNewPrivileges=true","PrivateTmp=true","ProtectSystem=full",
            "ProtectKernelTunables=true","ProtectControlGroups=true",
            "LockPersonality=true","ExecStart=/usr/bin/python3 ",
            "raeburn_command_centre.py work",
            "Environment=PATH="):
            self.assertIn(required,unit)
        self.assertIn("linux-worker/raeburn-ssh-bin",unit)
        self.assertNotIn("ExecStart=/bin/sh",unit)

if __name__=="__main__":
    unittest.main()
