"""Protected, host-owned memory-scoped workers and GPU watchdog regression tests."""
from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import managed_worker as m

class ManagedWorkerTests(unittest.TestCase):
    def setUp(self):
        self.job={'job_id':'a'*32,'task':'video','payload':{'file':'abc'},
            'attempt':1,'lease_token':'lease-token','resources':{
                'ram_mb':1024,'vram_mb':2048,'gpu_index':0}}
        self.handlers={'video':{'argv':['/usr/bin/python3','/opt/company/worker.py'],'timeout':120}}

    def test_command_contains_hard_memory_and_swap_scope_not_payload(self):
        unit,cmd=m.build_command(self.job,self.handlers['video'])
        self.assertEqual(unit,'open-ci-app-'+'a'*32+'.scope')
        self.assertIn('MemoryMax=1024M',cmd)
        self.assertIn('MemorySwapMax=0',cmd)
        self.assertIn('--scope',cmd)
        self.assertEqual(cmd[-2:],['/usr/bin/python3','/opt/company/worker.py'])
        self.assertNotIn('abc',' '.join(cmd))
        self.assertNotIn('/bin/sh',cmd)

    def test_invalid_app_memory_budget_fails_closed(self):
        self.job['resources']['ram_mb']=True
        with self.assertRaises(RuntimeError):
            m.build_command(self.job,self.handlers['video'])

    def test_manifest_requires_private_owner_owned_executable(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'handlers.json'
            path.write_text(json.dumps({'video':{'argv':['/usr/bin/python3','-V'],
                                                  'timeout':120}}))
            os.chmod(path,0o600)
            with patch('managed_worker.os.geteuid',return_value=path.stat().st_uid or 1000),\
                 patch('managed_worker.Path.is_file',return_value=True),\
                 patch('managed_worker.os.access',return_value=True):
                # On a privileged CI process the patched current UID must match.
                if path.stat().st_uid==0:
                    with self.assertRaises(PermissionError):
                        m.load_handlers(path)
                else:
                    self.assertIn('video',m.load_handlers(path))
            os.chmod(path,0o644)
            with self.assertRaises(PermissionError):
                m.load_handlers(path)

    def test_gpu_absence_blocks_execution_before_systemd(self):
        class Client:
            def complete(self,*args):
                return {'status':'queued','error':args[-1]['error']}
        with patch('managed_worker.subprocess.Popen') as proc:
            result=m.run_one(Client(),{'job':self.job},self.handlers,telemetry=lambda:{})
        self.assertIn('GPU vendor/mode',result['error'])
        proc.assert_not_called()

    def test_systemd_scope_unavailable_does_not_execute_unbounded(self):
        self.job['resources'].update({'vram_mb':0,'gpu_index':None})
        class Client:
            def complete(self,*args):
                return {'status':'queued','reason':args[-1]}
        with patch('managed_worker.subprocess.Popen',side_effect=OSError('dbus down')):
            value=m.run_one(Client(),{'job':self.job},self.handlers)
        self.assertIn('systemd user scope unavailable',str(value))

    def test_success_uses_preapproved_handler_and_reports_json(self):
        self.job['resources'].update({'vram_mb':0,'gpu_index':None})
        calls=[]
        class FakeProc:
            returncode=0
            def __init__(self,cmd,**kwargs):
                calls.append((cmd,kwargs))
            def poll(self):return None
            def communicate(self,input=None,timeout=None):
                calls.append(('input',json.loads(input)))
                return b'{"status":"ok"}',None
        class Client:
            def complete(self,*args):
                calls.append(('complete',args))
                return {'status':'completed'}
        with patch('managed_worker.subprocess.Popen',FakeProc):
            result=m.run_one(Client(),{'job':self.job},self.handlers)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(calls[1][1]['payload'],{'file':'abc'})
        self.assertTrue(calls[-1][1][2])
        self.assertIn('MemoryMax=1024M',calls[0][0])

    def test_non_allowlisted_task_never_runs(self):
        self.job['task']='unapproved'
        class Client:
            def complete(self,*args):return {'status':'queued'}
        with patch('managed_worker.subprocess.Popen') as run:
            m.run_one(Client(),{'job':self.job},self.handlers)
        run.assert_not_called()

if __name__=='__main__':
    unittest.main()
