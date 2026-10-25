import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app_runtime import Broker
from resource_guard import ResourceGate
import ci_bridge as bridge
import ci_job_control as control
from ci_execution import run_container, stop_container, workspace_bytes


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.broker=Broker(self.root/'broker')
        self.broker.resource_gate=ResourceGate(memory_reader=lambda:16000,gpu_reader=lambda:{},
            policy_reader=lambda:{},runner_reader=lambda:{'reserved_ram_mb':0,'active_runners':0,'reserved_cpus':0})
        self.repo='owner/repo'
        self.manifest={'schema':1,'repositories':{self.repo:{'provider':'github','branches':['main','test'],
          'image':'python@sha256:'+'a'*64,'ram_mb':256,'max_parallel':1,
          'steps':[{'name':'check','argv':['python','--version']}]}}}
        bridge.ensure_scheduler(self.manifest,self.broker)
        self.queued=bridge.enqueue('github',self.repo,'a'*40,'main','push',self.manifest,self.broker)
        self.job=self.queued['job_id']
        self.who={'role':'worker','app':bridge.app_for(self.repo),'tasks':['build']}

    def test_cancel_queued_is_durable_and_never_claimed(self):
        self.assertEqual(control.cancel(self.broker,self.job,'owner')['status'],'cancelled')
        self.assertIsNone(bridge.claim_one(self.manifest,self.broker)[0])
        self.assertEqual(Broker(self.root/'broker').inspect(self.who,self.job)['status'],'cancelled')

    def test_running_cancel_retains_capacity_until_confirmed_finish(self):
        job,_=bridge.claim_one(self.manifest,self.broker)
        control.cancel(self.broker,self.job,'owner')
        self.assertTrue(control.cancellation_requested(self.broker,self.job))
        self.assertEqual(self.broker.inspect(self.who,self.job)['status'],'leased')
        self.assertEqual(control.finish_cancel(self.broker,self.who,self.job,job['lease_token'])['status'],'cancelled')

    def test_cross_project_and_invalid_lease_cannot_finish_cancel(self):
        job,_=bridge.claim_one(self.manifest,self.broker)
        with self.assertRaises(PermissionError):
            control.finish_cancel(self.broker,{**self.who,'app':'other-app'},self.job,job['lease_token'])
        with self.assertRaises(PermissionError):
            control.finish_cancel(self.broker,self.who,self.job,'x'*50)
        self.assertEqual(self.broker.inspect(self.who,self.job)['status'],'leased')

    def test_same_sha_on_different_branches_is_distinct_work(self):
        other=bridge.enqueue('github',self.repo,'a'*40,'test','push',self.manifest,self.broker)
        self.assertNotEqual(self.job,other['job_id'])

    def test_failure_does_not_retry_side_effects_by_default(self):
        job,_=bridge.claim_one(self.manifest,self.broker)
        done=self.broker.complete(self.who,self.job,job['lease_token'],False,{'error':'test'},allow_retry=False)
        self.assertEqual(done['status'],'failed')

    def test_receipt_and_redacted_log_persist_after_reopen(self):
        receipt={'provider_status_reported':False,'commit':'a'*40,'steps':[{'exit_code':1}]}
        control.save_receipt(self.broker,self.job,receipt)
        logs=self.root/'logs';logs.mkdir();(logs/(self.job+'.log')).write_text('glpat-'+'a'*30+'\n')
        item=control.inspect(Broker(self.root/'broker'),self.root,self.job)
        self.assertEqual(item['receipt'],receipt)
        self.assertNotIn('glpat-',item['log'])

    def test_log_path_escape_and_symlinks_rejected(self):
        for job in ('../secret','x','a'*32+'/../secret'):
            with self.assertRaises(ValueError):control.inspect(self.broker,self.root,job)
        logs=self.root/'logs';logs.mkdir();(logs/(self.job+'.log')).symlink_to('/etc/passwd')
        with self.assertRaises(PermissionError):control.inspect(self.broker,self.root,self.job)

    def test_artifact_inventory_verifies_hash_and_quota(self):
        import hashlib
        work=self.root/'work';work.mkdir();(work/'test.txt').write_text('hello')
        items=control.collect_artifacts(work,self.root/'artifacts',['test.txt'])
        self.assertEqual(items[0]['sha256'],hashlib.sha256(b'hello').hexdigest())
        with self.assertRaises(RuntimeError):control.collect_artifacts(work,self.root/'small',['test.txt'],max_bytes=1)
        (work/'link').symlink_to('/etc/passwd')
        with self.assertRaises(PermissionError):control.collect_artifacts(work,self.root/'symlinks',['link'])
        with self.assertRaises(ValueError):control.collect_artifacts(work,self.root/'traversal',['../outside'])

    def test_successful_execution_receipt_tracks_status_reporting_failure(self):
        job,_=bridge.claim_one(self.manifest,self.broker)
        def response(argv,**kw):
            return subprocess.CompletedProcess(argv,0,'a'*40 if 'rev-parse' in argv else '')
        with patch.object(bridge,'STATE',self.root),patch.object(bridge,'run_cmd',side_effect=response),\
             patch.object(bridge,'provider_status',return_value=False),\
             patch.object(bridge,'monitored_container',return_value=(0,False)):
            result=bridge.execute_one(job,self.manifest,self.broker)
        self.assertEqual(result['status'],'passed')
        self.assertFalse(result['provider_status_reported'])
        self.assertEqual(result['steps'][0]['exit_code'],0)
        self.assertEqual(control.inspect(self.broker,self.root,self.job)['receipt']['commit'],'a'*40)

    def test_uncertain_kill_never_releases_lease_or_workspace(self):
        job,_=bridge.claim_one(self.manifest,self.broker)
        def response(argv,**kw):return subprocess.CompletedProcess(argv,0,'a'*40 if 'rev-parse' in argv else '')
        with patch.object(bridge,'STATE',self.root),patch.object(bridge,'run_cmd',side_effect=response),\
             patch.object(bridge,'provider_status',return_value=True),\
             patch.object(bridge,'monitored_container',return_value=(124,True)):
            result=bridge.execute_one(job,self.manifest,self.broker)
        self.assertEqual(result['status'],'stranded')
        self.assertEqual(self.broker.inspect(self.who,self.job)['status'],'leased')
        self.assertTrue((self.root/'work'/self.job).exists())

class ContainerMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.log=self.root/'job.log'
    def run(self,result=None):
        return super().run(result)
    def monitor(self,code,**opts):
        return run_container([sys.executable,'-c',code],cwd=self.root,timeout=3,
              log_path=self.log,heartbeat=lambda:True,container_name='open-ci-test',
              run=lambda argv,**kw:subprocess.CompletedProcess(argv,0,'false'),**opts)
    def test_split_secret_and_private_key_are_redacted_before_persisting(self):
        code="import sys,time;sys.stdout.write('glpat-');sys.stdout.flush();time.sleep(.05);print('a'*30);print('-----BEGIN RSA PRIVATE KEY-----');print('PRIVATEBODY');print('-----END RSA PRIVATE KEY-----')"
        self.assertEqual(self.monitor(code),(0,False))
        log=self.log.read_text();self.assertNotIn('glpat-',log);self.assertNotIn('PRIVATEBODY',log)
    def test_oversized_unterminated_line_is_not_persisted(self):
        self.assertEqual(self.monitor("print('a'*40000)"),(0,False))
        self.assertLess(self.log.stat().st_size,1000)
    def test_confirmed_cancel_kills_process_before_capacity_release(self):
        self.assertEqual(self.monitor('import time;time.sleep(10)',cancelled=lambda:True),(130,False))
    def test_missing_storage_causes_termination(self):
        self.assertEqual(self.monitor('import time;time.sleep(10)',workspace=self.root/'missing'),(126,False))
    def test_failed_inspect_is_not_proof_of_absent_container(self):
        def unavailable(argv,**kw):return subprocess.CompletedProcess(argv,1,'')
        self.assertFalse(stop_container('open-ci-test',unavailable))
    def test_symlink_workspace_size_does_not_follow_outside_files(self):
        (self.root/'outside').symlink_to('/proc/kcore')
        self.assertEqual(workspace_bytes(self.root,100),0)
    def test_output_quota_terminates_infinite_output(self):
        result=self.monitor("import sys;sys.stdout.write('x'*5000000)")
        self.assertEqual(result,(122,False));self.assertLessEqual(self.log.stat().st_size,4*1024*1024)
