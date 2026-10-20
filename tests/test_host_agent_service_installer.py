"""Regression: a user-service installer must produce a systemd-valid unit."""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
INSTALLER=ROOT/'scripts'/'install-host-agent-service.sh'

class HostAgentInstallerTests(unittest.TestCase):
    def test_script_has_valid_shell_syntax(self):
        result=subprocess.run(['bash','-n',str(INSTALLER)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_installer_repairs_invalid_existing_unit_and_verifies_it(self):
        with tempfile.TemporaryDirectory(prefix='open-ci-host-installer-') as td:
            top=Path(td)
            project=top/'open-ci-hub'
            (project/'scripts').mkdir(parents=True)
            (project/'host_agent.py').write_text('print("sample")\n')
            script=project/'scripts'/'install-host-agent-service.sh'
            shutil.copy2(INSTALLER,script)
            home=top/'home'
            units=home/'.config'/'systemd'/'user'
            units.mkdir(parents=True)
            unit=units/'open-ci-hub-host-agent.service'
            unit.write_text('[Service]\nWorkingDirectory="/bad/path"\n')
            fakebin=top/'fakebin'
            fakebin.mkdir()
            fake_id=fakebin/'id'
            fake_id.write_text('#!/bin/sh\nif [ "$1" = "-u" ]; then echo 1000; else /usr/bin/id "$@"; fi\n')
            fake_id.chmod(0o755)
            fake_ctl=fakebin/'systemctl'
            fake_ctl.write_text('#!/bin/sh\nexit 0\n')
            fake_ctl.chmod(0o755)
            env=os.environ.copy()
            env['HOME']=str(home)
            env['PATH']=str(fakebin)+os.pathsep+env.get('PATH','/usr/bin:/bin')
            result=subprocess.run(['bash',str(script)],cwd=project,env=env,
                                  capture_output=True,text=True,timeout=12)
            self.assertEqual(result.returncode,0,result.stderr)
            text=unit.read_text()
            self.assertIn('WorkingDirectory='+str(project),text)
            self.assertNotIn('WorkingDirectory="',text)
            self.assertIn('ExecStart="',text)
            self.assertNotIn('WorkingDirectory="/bad/path"',text)
            if shutil.which('systemd-analyze'):
                result=subprocess.run(['systemd-analyze','verify',str(unit)],
                                      capture_output=True,text=True,timeout=12)
                self.assertEqual(result.returncode,0,result.stderr)

    def test_installer_does_not_enable_or_start_service(self):
        body=INSTALLER.read_text()
        self.assertIn('systemctl --user daemon-reload',body)
        self.assertNotIn('systemctl --user enable --now\n',body)
        self.assertNotIn('systemctl --user start open-ci-hub-host-agent.service\n',body)

if __name__=='__main__':
    unittest.main()
