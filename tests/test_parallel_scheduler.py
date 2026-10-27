"""Parallel job admission, memory/VRAM reserves, concurrency and recovery."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from app_runtime import Broker
from resource_guard import ResourceGate, memory_available_mb, nvidia_vram_mb

class ParallelSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.b=Broker(Path(self.tmp.name)/'state')
        self.b.resource_gate=ResourceGate(memory_reader=lambda:16000,
             gpu_reader=lambda:{0:{'total_mb':8192,'free_mb':7500}})
        self.people={}
        for app,tasks in [('trg-recruitment',['cv','transform']),
                          ('raeburnemailer',['compose','send']),
                          ('clientmotive',['video','thumbnail'])]:
            self.b.register(app,tasks,confirm=True)
            self.people[app]={
                'submit':self.b.auth(self.b.issue(app,'submit',confirm=True)['token']),
                'worker':self.b.auth(self.b.issue(app,'worker',confirm=True)['token'])}

    def tearDown(self):self.tmp.cleanup()

    def put(self,app,task):
        return self.b.submit(self.people[app]['submit'],task,{'object_id':'123'})['job_id']

    def take(self,app):
        return self.b.claim(self.people[app]['worker'])

    def test_multiple_projects_execute_in_parallel_under_global_ceiling(self):
        a=self.put('trg-recruitment','cv')
        b=self.put('raeburnemailer','compose')
        c=self.put('clientmotive','thumbnail')
        for app,jid in [('trg-recruitment',a),('raeburnemailer',b),('clientmotive',c)]:
            actual=self.take(app)['job']
            self.assertEqual(actual['job_id'],jid)
            self.assertEqual(actual['resources']['ram_mb'],512)
        state=self.b.resource_status()
        self.assertEqual(state['active_jobs'],3)
        self.assertEqual(state['reserved_ram_mb'],1536)
        self.assertEqual(len(state['active_by_app']),3)

    def test_per_project_limits_prevent_starvation(self):
        for i in range(3):self.put('trg-recruitment','cv')
        for i in range(2):self.assertIsNotNone(self.take('trg-recruitment')['job'])
        self.assertEqual(self.take('trg-recruitment')['reason'],'project_parallel_limit')
        self.put('raeburnemailer','compose')
        self.assertIsNotNone(self.take('raeburnemailer')['job'])

    def test_global_concurrency_applies_across_projects(self):
        self.b.set_global_resources(2,2048,1024,confirm=True)
        self.put('trg-recruitment','cv');self.put('raeburnemailer','compose')
        self.put('clientmotive','thumbnail')
        self.assertIsNotNone(self.take('trg-recruitment')['job'])
        self.assertIsNotNone(self.take('raeburnemailer')['job'])
        self.assertEqual(self.take('clientmotive')['reason'],'global_parallel_limit')

    def test_ram_capacity_headroom_prevents_oversubscription(self):
        self.b.resource_gate=ResourceGate(memory_reader=lambda:8192,gpu_reader=lambda:{})
        self.b.set_task_budget('trg-recruitment','cv',4500,confirm=True)
        self.b.set_task_budget('raeburnemailer','compose',4500,confirm=True)
        self.put('trg-recruitment','cv');self.put('raeburnemailer','compose')
        self.assertIsNotNone(self.take('trg-recruitment')['job'])
        self.assertEqual(self.take('raeburnemailer')['reason'],'ram_headroom')
        self.assertEqual(self.b.resource_status()['reserved_ram_mb'],4500)

    def test_gpu_allocation_respects_reserved_vram_and_headroom(self):
        self.b.set_task_budget('clientmotive','video',1024,4096,0,confirm=True)
        self.b.set_task_budget('trg-recruitment','transform',1024,4096,0,confirm=True)
        self.put('clientmotive','video');self.put('trg-recruitment','transform')
        one=self.take('clientmotive')['job']
        self.assertEqual(one['resources']['gpu_index'],0)
        self.assertEqual(one['resources']['vram_mb'],4096)
        self.assertTrue(one['requires_ram_enforcement'])
        self.assertEqual(self.take('trg-recruitment')['reason'],'vram_headroom')
        self.assertEqual(self.b.resource_status()['reserved_vram_mb'][0],4096)

    def test_cpu_jobs_not_blocked_behind_gpu_job_if_vram_unavailable(self):
        self.b.resource_gate=ResourceGate(memory_reader=lambda:16000,gpu_reader=lambda:{})
        self.b.set_task_budget('clientmotive','video',1024,4096,0,confirm=True)
        self.put('clientmotive','video')
        cpu=self.put('clientmotive','thumbnail')
        actual=self.take('clientmotive')
        self.assertEqual(actual['job']['job_id'],cpu)
        self.assertEqual(self.take('clientmotive')['reason'],'gpu_sensor_unavailable')

    def test_missing_ram_telemetry_fails_closed(self):
        self.b.resource_gate=ResourceGate(memory_reader=lambda:None,gpu_reader=lambda:{})
        self.put('raeburnemailer','compose')
        self.assertEqual(self.take('raeburnemailer')['reason'],'ram_sensor_unavailable')

    def test_leased_job_blocks_resource_changes_and_stranded_keeps_reservations(self):
        self.b.set_task_budget('clientmotive','video',1024,3500,0,confirm=True)
        job=self.put('clientmotive','video')
        self.take('clientmotive')
        with self.assertRaises(RuntimeError):
            self.b.set_task_budget('clientmotive','video',2048,4000,0,confirm=True)
        with self.b._db() as db:
            db.execute('UPDATE jobs SET lease_until=? WHERE id=?',
                       (time.time()-1,job))
        self.assertEqual(self.take('clientmotive')['reason'],'queue_empty')
        self.assertEqual(self.b.inspect(self.people['clientmotive']['submit'],job)['status'],'stranded')
        self.assertEqual(self.b.resource_status()['reserved_vram_mb'][0],3500)
        with self.assertRaises(RuntimeError):
            self.b.set_task_budget('clientmotive','video',2048,4000,0,confirm=True)
        self.b.recover_stranded(job,confirm=True)
        self.b.set_task_budget('clientmotive','video',2048,4000,0,confirm=True)

    def test_atomic_claims_cannot_exceed_parallel_limit(self):
        self.b.set_parallel('raeburnemailer',1,confirm=True)
        for i in range(4):self.put('raeburnemailer','compose')
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:self.take('raeburnemailer'),range(4)))
        self.assertEqual(sum(1 for x in results if x.get('job')),1)
        self.assertEqual(self.b.resource_status()['active_jobs'],1)

    def test_limits_require_local_owner_and_validate_types(self):
        with self.assertRaises(PermissionError):
            self.b.set_task_budget('clientmotive','video',1000)
        with self.assertRaises(ValueError):
            self.b.set_task_budget('clientmotive','video',1000,3000,None,confirm=True)
        with self.assertRaises(ValueError):
            self.b.set_task_budget('clientmotive','video',1000,0,0,confirm=True)
        with self.assertRaises(ValueError):
            self.b.set_task_budget('clientmotive','video',True,confirm=True)
        with self.assertRaises(ValueError):
            self.b.set_global_resources(100,1,0,confirm=True)
        with self.assertRaises(PermissionError):
            self.b.set_parallel('trg-recruitment',4)

    def test_nonexistent_gpu_profile_does_not_allocate(self):
        self.b.resource_gate=ResourceGate(memory_reader=lambda:16000,gpu_reader=lambda:{
            0:{'total_mb':8192,'free_mb':7000}})
        self.b.set_task_budget('clientmotive','video',512,2000,1,confirm=True)
        self.put('clientmotive','video')
        self.assertEqual(self.take('clientmotive')['reason'],'gpu_sensor_unavailable')

class HardwareProbeTests(unittest.TestCase):
    def test_nvidia_probe_rejects_malformed_results(self):
        class Result:
            returncode=0
            stdout='0, 12288, 9012\n1, 12288, 4012\n'
        with patch('resource_guard.subprocess.run',return_value=Result()):
            g=nvidia_vram_mb()
        self.assertEqual(g[0]['free_mb'],9012)
        self.assertEqual(g[1]['total_mb'],12288)
        class Bad:
            returncode=0
            stdout='0, wrong, 9000\n'
        with patch('resource_guard.subprocess.run',return_value=Bad()):
            self.assertEqual(nvidia_vram_mb(),{})

    def test_ram_probe_returns_safe_integer_or_unknown(self):
        observed=memory_available_mb()
        self.assertTrue(observed is None or (isinstance(observed,int) and observed>=0))

from tests.test_web_console import DashboardHTTPTests

class ParallelHTTPTests(DashboardHTTPTests):
    def test_runtime_inventory_includes_resource_reservations(self):
        self.console.apps.resource_gate=ResourceGate(memory_reader=lambda:16384,
             gpu_reader=lambda:{0:{'total_mb':12288,'free_mb':10000}})
        status,payload,_=self.request('GET','/api/apps/inventory',cookie=self.session())
        self.assertEqual(status,200)
        resources=payload['resources']
        self.assertEqual(resources['capacity']['max_parallel'],4)
        self.assertEqual(resources['memory_available_mb'],16384)
        self.assertEqual(resources['trust'].split(';')[0],'admission-only')

if __name__=='__main__':
    unittest.main()
