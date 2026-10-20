"""Intel Iris Xe shared-RAM accounting and user memory safeguard regression tests."""
import json
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app_runtime import Broker
import resource_guard as rg
import managed_worker as mw

INTEL={0:{'vendor':'intel','mode':'shared','model':'Intel Iris Xe integrated (Tiger Lake)',
          'total_mb':None,'free_mb':None}}
NVIDIA={0:{'vendor':'nvidia','mode':'dedicated','total_mb':12288,'free_mb':10000}}

class IntelSharedGPUHardwareTests(unittest.TestCase):
    def test_tiger_lake_intel_iris_xe_discovered_without_fake_vram(self):
        with tempfile.TemporaryDirectory() as td:
            card=Path(td)/'card0'
            (card/'device').mkdir(parents=True)
            (card/'device'/'vendor').write_text('0x8086\n')
            (card/'device'/'device').write_text('0x9a49\n')
            with patch('resource_guard.Path.glob',return_value=[card]):
                detected=rg.intel_iris_xe_shared_gpu()
        self.assertEqual(detected[0]['mode'],'shared')
        self.assertIsNone(detected[0]['free_mb'])
        self.assertIsNone(detected[0]['total_mb'])

    def test_unknown_intel_gpu_does_not_get_guessed_shared_mode(self):
        with tempfile.TemporaryDirectory() as td:
            card=Path(td)/'card0'
            (card/'device').mkdir(parents=True)
            (card/'device'/'vendor').write_text('0x8086')
            (card/'device'/'device').write_text('0x56a0')
            with patch('resource_guard.Path.glob',return_value=[card]):
                self.assertEqual(rg.intel_iris_xe_shared_gpu(),{})

    def test_nvidia_dedicated_memory_unchanged(self):
        with patch('resource_guard.nvidia_vram_mb',return_value={
            0:{'total_mb':4096,'free_mb':2500}}):
            gpu=rg.gpu_memory_inventory()
        self.assertEqual(gpu[0]['mode'],'dedicated')
        self.assertEqual(gpu[0]['free_mb'],2500)

class SharedRamSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.b=Broker(Path(self.tmp.name)/'state')
        self.b.resource_gate=rg.ResourceGate(memory_reader=lambda:15000,gpu_reader=lambda:INTEL)
        self.people={}
        for app,task in [('clientmotive','video'),('trg-recruitment','cv'),
                         ('raeburnemailer','prepare')]:
            self.b.register(app,[task],confirm=True)
            self.people[app]={
                'submit':self.b.auth(self.b.issue(app,'submit',confirm=True)['token']),
                'worker':self.b.auth(self.b.issue(app,'worker',confirm=True)['token'])}
        self.b.set_task_budget('clientmotive','video',2048,4096,0,confirm=True)
        self.b.set_task_budget('trg-recruitment','cv',1024,confirm=True)
        self.b.set_task_budget('raeburnemailer','prepare',512,confirm=True)
        self.memory_patch=patch('resource_guard.physical_ram_total_mb',return_value=15*1024)
        self.memory_patch.start()

    def tearDown(self):
        self.memory_patch.stop()
        self.tmp.cleanup()

    def queue(self,app,task):
        return self.b.submit(self.people[app]['submit'],task,{'request_id':'id_0001'})

    def take(self,app):
        return self.b.claim(self.people[app]['worker'])

    def test_laptop_defaults_protect_os_and_limit_parallelism(self):
        status=self.b.resource_status()
        self.assertEqual(status['capacity']['min_free_ram_mb'],4096)
        self.assertEqual(status['capacity']['max_parallel'],3)
        self.assertEqual(status['gpu'][0]['vendor'],'intel')
        self.assertIsNone(status['gpu'][0]['free_mb'])

    def test_gpu_reserves_ram_plus_shared_gpu_working_set(self):
        self.queue('clientmotive','video')
        job=self.take('clientmotive')['job']
        self.assertEqual(job['resources']['gpu_memory_mode'],'shared')
        self.assertEqual(job['resources']['effective_ram_mb'],6144)
        self.assertEqual(job['resources']['min_free_ram_mb'],4096)
        state=self.b.resource_status()
        self.assertEqual(state['reserved_ram_mb'],6144)
        self.assertEqual(state['reserved_shared_gpu_ram_mb'],4096)
        self.assertEqual(state['reserved_vram_mb'],{})
        self.queue('trg-recruitment','cv')
        self.assertIsNotNone(self.take('trg-recruitment')['job'])

    def test_integrated_gpu_jobs_block_each_other_but_cpu_continues(self):
        self.b.set_task_budget('trg-recruitment','cv',1024,2048,0,confirm=True)
        self.queue('clientmotive','video')
        self.queue('trg-recruitment','cv')
        self.queue('raeburnemailer','prepare')
        self.assertIsNotNone(self.take('clientmotive')['job'])
        self.assertEqual(self.take('trg-recruitment')['reason'],'integrated_gpu_busy')
        self.assertIsNotNone(self.take('raeburnemailer')['job'])

    def test_ram_headroom_stops_intel_gpu_job_not_cpu(self):
        self.b.resource_gate=rg.ResourceGate(memory_reader=lambda:9000,gpu_reader=lambda:INTEL)
        self.queue('clientmotive','video')
        self.assertEqual(self.take('clientmotive')['reason'],'ram_headroom')
        self.queue('raeburnemailer','prepare')
        self.assertIsNotNone(self.take('raeburnemailer')['job'])

    def test_shared_reservation_survives_intel_sensor_loss(self):
        self.queue('clientmotive','video')
        self.take('clientmotive')
        self.b.resource_gate=rg.ResourceGate(memory_reader=lambda:15000,gpu_reader=lambda:{})
        info=self.b.resource_status()
        self.assertEqual(info['reserved_ram_mb'],6144)
        self.assertEqual(info['reserved_shared_gpu_ram_mb'],4096)
        self.queue('trg-recruitment','cv')
        self.assertIsNotNone(self.take('trg-recruitment')['job'])

    def test_host_owner_can_override_conservative_defaults(self):
        self.b.set_global_resources(2,6144,0,confirm=True)
        state=self.b.resource_status()
        self.assertEqual(state['capacity']['max_parallel'],2)
        self.assertEqual(state['capacity']['min_free_ram_mb'],6144)

class IntelManagedWorkerTests(unittest.TestCase):
    def setUp(self):
        self.job={'job_id':'c'*32,'task':'video','payload':{'id':'safe'},
          'attempt':1,'lease_token':'lease-key',
          'resources':{'ram_mb':2048,'vram_mb':4096,'gpu_index':0,
                       'gpu_memory_mode':'shared','min_free_ram_mb':4096}}
        self.handlers={'video':{'argv':['/usr/bin/python3','/tmp/approved-worker.py'],
                                  'timeout':120}}
        self.approved=INTEL

    def test_shared_gpu_cgroup_limit_includes_both_allocations(self):
        unit,argv=mw.build_command(self.job,self.handlers['video'])
        self.assertIn('MemoryMax=6144M',argv)
        self.assertIn('MemorySwapMax=0',argv)
        self.assertNotIn('CUDA_VISIBLE_DEVICES',argv)
    
    def test_intel_gpu_rejects_low_ram_before_start(self):
        class Client:
            def complete(self,*args):return {'status':'queued','error':args[-1]['error']}
        with patch('managed_worker.subprocess.Popen') as process:
            result=mw.run_one(Client(),{'job':self.job},self.handlers,
                              telemetry=lambda:self.approved,ram_telemetry=lambda:7000)
        self.assertIn('system RAM headroom',result['error'])
        process.assert_not_called()

    def test_vendor_mismatch_blocks_launch(self):
        class Client:
            def complete(self,*args):return {'status':'queued','error':args[-1]['error']}
        with patch('managed_worker.subprocess.Popen') as process:
            result=mw.run_one(Client(),{'job':self.job},self.handlers,
                              telemetry=lambda:NVIDIA,ram_telemetry=lambda:15000)
        self.assertIn('vendor/mode',result['error'])
        process.assert_not_called()

    def test_intel_worker_uses_shared_ram_no_cuda_env(self):
        calls=[]
        class FakeProc:
            returncode=0
            def __init__(self,args,**kwargs):
                calls.append(('start',args,kwargs))
                self.stdin=io.BytesIO()
                self.stdout=io.BytesIO(b'{"success":true}')
            def poll(self):return 0
            def wait(self,timeout=None):return 0
        class Client:
            def complete(self,*args):return {'status':'completed'}
        with patch('managed_worker.subprocess.Popen',FakeProc):
            result=mw.run_one(Client(),{'job':self.job},self.handlers,
                telemetry=lambda:INTEL,ram_telemetry=lambda:15000)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(calls[0][0],'start')
        env=calls[0][2]['env']
        self.assertEqual(env.get('OPEN_CI_INTEL_SHARED_GPU'),'1')
        self.assertNotIn('CUDA_VISIBLE_DEVICES',env)

if __name__=='__main__':
    unittest.main()
