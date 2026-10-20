"""Parallel project scheduling: real contention, caps, VRAM and stale claims."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import app_runtime
import parallel_scheduler as parallel
import resource_probe

def telemetry(ram=12000,vram=7000,now=None):
    return {'memory_available_mb':ram,'vram_available_mb':vram,
            'observed_at':now if now is not None else time.time()}

class ParallelSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.b=app_runtime.Broker(Path(self.temp.name))
        self.b.register('raeburnemailer',['dispatch','prepare'],confirm=True)
        self.b.register('trg-recruitment',['cv-process'],confirm=True)
        self.b.register('clientmotive',['video-render'],confirm=True)
        self.b.configure_node('garuda-pc',16384,8192,8,4,confirm=True)
        self.b.configure_task('raeburnemailer','dispatch',2048,0,1,confirm=True)
        self.b.configure_task('raeburnemailer','prepare',1024,0,1,confirm=True)
        self.b.configure_task('trg-recruitment','cv-process',2048,0,2,confirm=True)
        self.b.configure_task('clientmotive','video-render',4096,5000,4,confirm=True)
        self.b.configure_app_limit('raeburnemailer',2,confirm=True)
        self.b.configure_app_limit('trg-recruitment',2,confirm=True)
        self.b.configure_app_limit('clientmotive',1,confirm=True)
        self.send={}
        self.worker={}
        for app in ('raeburnemailer','trg-recruitment','clientmotive'):
            self.send[app]=self.b.auth(self.b.issue(app,'submit',confirm=True)['token'])
            self.worker[app]=self.b.auth(self.b.issue(app,'worker',node='garuda-pc',confirm=True)['token'])

    def tearDown(self):self.temp.cleanup()

    def test_multiple_projects_claim_concurrently_and_share_one_memory_budget(self):
        apps=['raeburnemailer','trg-recruitment','clientmotive']
        tasks=['dispatch','cv-process','video-render']
        for app,task in zip(apps,tasks):
            self.b.submit(self.send[app],task,{'id':app})
        def take(app):
            return self.b.claim(self.worker[app],telemetry())
        with ThreadPoolExecutor(max_workers=3) as e:
            got=list(e.map(take,apps))
        self.assertTrue(all(v['job'] for v in got))
        inventory=self.b.inventory()
        pc=inventory['nodes'][0]
        self.assertEqual(pc['active_jobs'],3)
        self.assertEqual(pc['reserved_memory_mb'],8192)
        self.assertEqual(pc['reserved_vram_mb'],5000)
        self.assertEqual(pc['reserved_cpu_slots'],7)
        self.assertEqual(pc['available_memory_mb'],4096)
        self.assertEqual(pc['available_vram_mb'],2168)
        self.assertEqual(pc['available_cpu_slots'],1)
        for app,v in zip(apps,got):
            self.b.complete(self.worker[app],v['job']['job_id'],v['job']['lease_token'],True,{'ok':True})
        self.assertEqual(self.b.inventory()['nodes'][0]['active_jobs'],0)

    def test_vram_exhaustion_keeps_gpu_job_queued_without_starving_cpu(self):
        app='clientmotive'
        self.b.submit(self.send[app],'video-render',{'video':1})
        self.b.submit(self.send[app],'video-render',{'video':2})
        first=self.b.claim(self.worker[app],telemetry())['job']
        self.assertEqual(first['reservation']['vram_mb'],5000)
        blocked=self.b.claim(self.worker[app],telemetry())
        self.assertIsNone(blocked['job'])
        self.assertIn(blocked['reason'],('app_concurrency_limit','resource_unavailable_or_profile_missing'))
        self.b.submit(self.send['raeburnemailer'],'prepare',{'id':2})
        cpu=self.b.claim(self.worker['raeburnemailer'],telemetry())['job']
        self.assertEqual(cpu['reservation']['vram_mb'],0)
        self.assertEqual(self.b.inventory()['nodes'][0]['active_jobs'],2)

    def test_gpu_without_verified_available_vram_is_denied(self):
        self.b.submit(self.send['clientmotive'],'video-render',{'task':1})
        self.assertIsNone(self.b.claim(self.worker['clientmotive'],telemetry(vram=0))['job'])
        # Need a reserve of at least 1024 memory and 1024 VRAM here.
        self.assertIsNone(self.b.claim(self.worker['clientmotive'],telemetry(vram=5500))['job'])
        self.assertIsNotNone(self.b.claim(self.worker['clientmotive'],telemetry(vram=6200))['job'])

    def test_no_telemetry_or_stale_telemetry_fails_closed(self):
        self.b.submit(self.send['raeburnemailer'],'prepare',{})
        for snapshot in (None,{'memory_available_mb':9000,'vram_available_mb':9000},
                         telemetry(now=time.time()-40)):
            with self.subTest(snapshot=snapshot),self.assertRaises(ValueError):
                self.b.claim(self.worker['raeburnemailer'],snapshot)
        self.assertEqual(self.b.inventory()['nodes'][0]['active_jobs'],0)

    def test_legacy_unbound_worker_token_cannot_bypass_budget(self):
        self.b.submit(self.send['raeburnemailer'],'prepare',{})
        who={'app':'raeburnemailer','role':'worker','tasks':['prepare']}
        with self.assertRaises(PermissionError):
            self.b.claim(who,telemetry())
        with self.assertRaises(ValueError):
            self.b.issue('raeburnemailer','worker',confirm=True)

    def test_unprofiled_tasks_remain_queued_until_owner_profiles(self):
        self.b.register('new-project',['task-one'],confirm=True)
        producer=self.b.auth(self.b.issue('new-project','submit',confirm=True)['token'])
        worker=self.b.auth(self.b.issue('new-project','worker',node='garuda-pc',confirm=True)['token'])
        self.b.submit(producer,'task-one',{'id':1})
        claimed=self.b.claim(worker,telemetry())
        self.assertIsNone(claimed['job'])
        self.assertEqual(claimed['reason'],'resource_unavailable_or_profile_missing')
        self.b.configure_task('new-project','task-one',512,0,1,confirm=True)
        self.assertIsNotNone(self.b.claim(worker,telemetry())['job'])

    def test_app_concurrency_limits_prevent_one_project_monopolising(self):
        for idx in range(3):
            self.b.submit(self.send['raeburnemailer'],'prepare',{'index':idx})
        claims=[self.b.claim(self.worker['raeburnemailer'],telemetry()) for _ in range(3)]
        self.assertTrue(claims[0]['job'] and claims[1]['job'])
        self.assertIsNone(claims[2]['job'])
        self.assertEqual(claims[2]['reason'],'app_concurrency_limit')
        self.b.submit(self.send['trg-recruitment'],'cv-process',{})
        self.assertIsNotNone(self.b.claim(self.worker['trg-recruitment'],telemetry())['job'])

    def test_stale_reservations_expire_and_old_lease_cannot_ack(self):
        j=self.b.submit(self.send['clientmotive'],'video-render',{})
        first=self.b.claim(self.worker['clientmotive'],telemetry())['job']
        with self.b._db() as db:
            db.execute('UPDATE jobs SET lease_until=? WHERE id=?',(time.time()-2,j['job_id']))
        other=self.b.claim(self.worker['clientmotive'],telemetry())['job']
        self.assertEqual(other['attempt'],2)
        self.assertEqual(self.b.inventory()['nodes'][0]['active_jobs'],1)
        with self.assertRaises(PermissionError):
            self.b.complete(self.worker['clientmotive'],first['job_id'],first['lease_token'],True,{})
        self.b.complete(self.worker['clientmotive'],other['job_id'],other['lease_token'],True,{})
        self.assertEqual(self.b.inventory()['nodes'][0]['active_jobs'],0)

    def test_profile_cannot_change_during_active_execution(self):
        self.b.submit(self.send['raeburnemailer'],'prepare',{})
        claim=self.b.claim(self.worker['raeburnemailer'],telemetry())['job']
        with self.assertRaises(RuntimeError):
            self.b.configure_task('raeburnemailer','prepare',3000,0,1,confirm=True)
        with self.assertRaises(ValueError):
            self.b.configure_app_limit('raeburnemailer',0,confirm=True)
        self.b.submit(self.send['raeburnemailer'],'prepare',{'second':True})
        second=self.b.claim(self.worker['raeburnemailer'],telemetry())['job']
        self.assertIsNotNone(second)
        with self.assertRaises(RuntimeError):
            self.b.configure_app_limit('raeburnemailer',1,confirm=True)
        self.b.complete(self.worker['raeburnemailer'],second['job_id'],second['lease_token'],True,{})
        self.b.complete(self.worker['raeburnemailer'],claim['job_id'],claim['lease_token'],True,{})
        self.b.configure_task('raeburnemailer','prepare',3000,0,1,confirm=True)

    def test_os_safety_reserves_default_nonzero_and_node_conflicts_denied(self):
        pc=self.b.inventory()['nodes'][0]
        self.assertEqual(pc['reserve_memory_mb'],4096)
        self.assertEqual(pc['reserve_vram_mb'],1024)
        with self.assertRaises(RuntimeError):
            self.b.configure_node('garuda-pc',16384,8192,8,4,confirm=True)
        with self.assertRaises(PermissionError):
            self.b.configure_node('another',8192,0,4,2)
        with self.assertRaises(ValueError):
            self.b.configure_node('another',1024,0,4,2,confirm=True)

    def test_cpu_only_nodes_allow_parallel_non_gpu_jobs(self):
        self.b.configure_node('cpu-worker',8192,0,4,2,confirm=True)
        w=self.b.auth(self.b.issue('raeburnemailer','worker',node='cpu-worker',confirm=True)['token'])
        self.b.submit(self.send['raeburnemailer'],'prepare',{})
        self.assertIsNotNone(self.b.claim(w,telemetry(vram=0))['job'])
        self.assertEqual(self.b.inventory()['nodes'][1]['node'],'garuda-pc')

class RealWorkerProbes(unittest.TestCase):
    def test_nvidia_absence_returns_zero_vram(self):
        with patch('resource_probe.subprocess.run',side_effect=FileNotFoundError):
            self.assertEqual(resource_probe._nvidia_free_mb(),0)

    def test_linux_free_memory_bounded_by_cgroup(self):
        with patch('resource_probe._host_memory_mb',return_value=10000),\
             patch('resource_probe._cgroup_headroom_mb',return_value=3000),\
             patch('resource_probe._nvidia_free_mb',return_value=2500):
            now=resource_probe.sample()
        self.assertEqual(now['memory_available_mb'],3000)
        self.assertEqual(now['vram_available_mb'],2500)

    def test_tampered_worker_resource_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            parallel.validate_telemetry(telemetry(now=time.time()+120),True)

from tests.test_web_console import DashboardHTTPTests

class ParallelHTTPTests(DashboardHTTPTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.console.apps.register('parallel-http',['run'],confirm=True)
        cls.console.apps.configure_node('http-node',8192,0,4,3,confirm=True)
        cls.console.apps.configure_task('parallel-http','run',1024,0,1,confirm=True)
        cls.submit=cls.console.apps.issue('parallel-http','submit',confirm=True)['token']
        cls.worker=cls.console.apps.issue('parallel-http','worker',node='http-node',confirm=True)['token']

    def test_http_worker_claim_requires_real_observations(self):
        header={'Authorization':'Bearer '+self.worker}
        submit={'Authorization':'Bearer '+self.submit}
        status,job,_=self.request('POST','/api/v1/jobs',
                  {'task':'run','payload':{'action':'sample'}},headers=submit)
        self.assertEqual(status,201)
        status,body,_=self.request('POST','/api/v1/workers/claim',{},headers=header)
        self.assertEqual(status,400)
        status,data,_=self.request('POST','/api/v1/workers/claim',
                {'telemetry':telemetry(ram=9000,vram=0)},headers=header)
        self.assertEqual(status,200)
        self.assertEqual(data['job']['reservation']['node'],'http-node')
        status,result,_=self.request('POST','/api/v1/workers/complete',{
            'job_id':data['job']['job_id'],'lease_token':data['job']['lease_token'],
            'success':True,'result':{'ok':True}},headers=header)
        self.assertEqual(status,200)

if __name__=='__main__':
    unittest.main()
