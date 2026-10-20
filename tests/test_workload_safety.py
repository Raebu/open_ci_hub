"""Unified workload safety: CI reservations, adaptive mode, priorities, emergency."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import machine_policy as mp
import native_runner_observer as ci
import resource_guard as rg
import managed_worker as mw
from app_runtime import Broker

def healthy(**kwargs):
    return {'temperature_c':45,'memory_pressure_pct':0,'load_per_cpu':0.2,
            'battery':{'percent':100,'discharging':False},**kwargs}

class PolicyTests(unittest.TestCase):
    def test_quiet_and_balanced_have_different_parallel_caps(self):
        self.assertEqual(mp.admission('quiet',healthy(),3)['max_parallel'],1)
        self.assertEqual(mp.admission('balanced',healthy(),3)['max_parallel'],3)
        self.assertEqual(mp.admission('performance',healthy(),3)['max_parallel'],3)

    def test_critical_heat_pressure_and_battery_pause_work(self):
        self.assertEqual(mp.admission('balanced',{'temperature_c':90},3)['reason'],'critical_thermal')
        self.assertEqual(mp.admission('balanced',{'temperature_c':90},3)['max_parallel'],0)
        self.assertEqual(mp.admission('balanced',{'memory_pressure_pct':25},3)['max_parallel'],0)
        self.assertEqual(mp.admission('balanced',{'battery':{'percent':10,'discharging':True}},3)['max_parallel'],0)
        self.assertEqual(mp.admission('balanced',{'battery':{'percent':9,'discharging':False}},3)['max_parallel'],3)

    def test_intermediate_cpu_heat_and_memory_pressure_reduce_concurrency(self):
        self.assertEqual(mp.admission('balanced',{'temperature_c':85},3)['max_parallel'],1)
        self.assertEqual(mp.admission('balanced',{'memory_pressure_pct':12},3)['max_parallel'],1)
        self.assertEqual(mp.admission('balanced',{'load_per_cpu':1.9},3)['max_parallel'],1)
        self.assertEqual(mp.admission('balanced',{},3)['max_parallel'],3)

    def test_sensor_reads_missing_are_not_fabricated(self):
        with patch('machine_policy.Path.glob',return_value=[]):
            self.assertIsNone(mp.package_temperature_c())
            self.assertIsNone(mp.battery_state())

class NativeObserverTests(unittest.TestCase):
    @staticmethod
    def respond(stdout='',returncode=0):
        return subprocess.CompletedProcess([],returncode,stdout,'')
    def test_accounts_for_all_known_runner_types_without_guessing_names(self):
        names='open-ci-github-runner\nopen-ci-gitlab-runner\nopen-ci-circleci-runner\nopen-ci-hub-pool-0123456789abcdef\nunrelated-app\n'
        inspected='\n'.join('/'+x+' 2147483648 1500000000' for x in names.splitlines()[:4])
        with patch('native_runner_observer.shutil.which',return_value='/usr/bin/docker'),\
             patch('native_runner_observer.subprocess.run',side_effect=[
                 self.respond(names),self.respond(inspected)]) as runner:
            result=ci.runner_reservations()
        self.assertEqual(result['active_runners'],4)
        self.assertEqual(result['reserved_ram_mb'],8192)
        self.assertEqual(result['reserved_cpus'],6.0)
        self.assertEqual(runner.call_count,2)
    def test_unbounded_runner_or_docker_failure_fail_closed(self):
        with patch('native_runner_observer.shutil.which',return_value='/usr/bin/docker'),\
             patch('native_runner_observer.subprocess.run',side_effect=[
                  self.respond('open-ci-github-runner\n'),
                  self.respond('/open-ci-github-runner 0 0')]):
            self.assertIsNone(ci.runner_reservations())
        with patch('native_runner_observer.shutil.which',return_value='/usr/bin/docker'),\
             patch('native_runner_observer.subprocess.run',
                   return_value=self.respond('',1)):
            self.assertIsNone(ci.runner_reservations())

class UnifiedBrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.b=Broker(Path(self.temp.name)/'.local')
        self.b.resource_gate=rg.ResourceGate(memory_reader=lambda:12000,
            gpu_reader=lambda:{},
            policy_reader=lambda:healthy(),
            runner_reader=lambda:{
                'reserved_ram_mb':4096,'active_runners':2,'reserved_cpus':3})
        self.b.register('myapp',['build','report'],confirm=True)
        self.submit=self.b.auth(self.b.issue('myapp','submit',confirm=True)['token'])
        self.worker=self.b.auth(self.b.issue('myapp','worker',confirm=True)['token'])
    def tearDown(self):self.temp.cleanup()
    def enqueue(self,task):
        return self.b.submit(self.submit,task,{'hello':True})['job_id']

    def test_native_runner_memory_counts_against_app_admission(self):
        self.b.set_task_budget('myapp','build',7000,confirm=True)
        self.enqueue('build')
        denied=self.b.claim(self.worker)
        self.assertIsNone(denied['job'])
        self.assertEqual(denied['reason'],'ram_headroom')
        info=self.b.resource_status()
        self.assertEqual(info['reserved_native_runner_ram_mb'],4096)
        self.assertEqual(info['native_runners'],2)

    def test_pause_resume_quiet_and_performance_are_persistent(self):
        self.enqueue('report')
        self.b.control(paused=True,confirm=True)
        self.assertEqual(self.b.claim(self.worker)['reason'],'admissions_paused')
        with self.b._db() as db:
            self.assertTrue(rg.ResourceGate.controls(db)['paused'])
        self.b.control(paused=False,mode='quiet',confirm=True)
        self.assertEqual(self.b.resource_status()['policy']['max_parallel'],1)
        self.assertIsNotNone(self.b.claim(self.worker)['job'])
        self.b.control(mode='performance',confirm=True)
        self.assertEqual(self.b.resource_status()['policy']['mode'],'performance')
    def test_worker_priority_claim_highest_queue_first(self):
        first=self.enqueue('build')
        second=self.enqueue('report')
        self.b.reprioritize(second,5,confirm=True)
        self.assertEqual(self.b.claim(self.worker)['job']['job_id'],second)
        with self.assertRaises(RuntimeError):
            self.b.reprioritize(second,0,confirm=True)
        self.assertEqual([x for x in self.b.queue_preview()['jobs']
                          if x['status']=='queued'][0]['job_id'],first)

    def test_invalid_priority_and_mode_require_confirmation(self):
        self.enqueue('build')
        with self.assertRaises(PermissionError):self.b.control(paused=True)
        with self.assertRaises(ValueError):self.b.control(mode='superuser',confirm=True)
        with self.assertRaises(ValueError):self.b.reprioritize('a'*32,100,confirm=True)
        with self.assertRaises(PermissionError):self.b.emergency_stop()

    def test_unknown_docker_prevents_unmonitored_new_app_claims(self):
        self.b.resource_gate=rg.ResourceGate(memory_reader=lambda:12000,
            gpu_reader=lambda:{},policy_reader=lambda:healthy(),
            runner_reader=lambda:None)
        self.enqueue('build')
        self.assertEqual(self.b.claim(self.worker)['reason'],'native_runner_telemetry_unavailable')

    def test_critical_thermal_pressure_suspends_new_jobs(self):
        self.b.resource_gate=rg.ResourceGate(memory_reader=lambda:12000,
            gpu_reader=lambda:{},policy_reader=lambda:{'temperature_c':95},
            runner_reader=lambda:{'reserved_ram_mb':0,'active_runners':0,'reserved_cpus':0})
        self.enqueue('report')
        self.assertEqual(self.b.claim(self.worker)['reason'],'critical_thermal')

    def test_reconcile_stranded_without_scope_proof_never_releases(self):
        job=self.enqueue('report')
        self.b.claim(self.worker)
        with self.b._db() as db:
            db.execute('UPDATE jobs SET status="stranded" WHERE id=?',(job,))
        class Fake:
            def __init__(self,returncode,stdout):
                self.returncode=returncode;self.stdout=stdout
        with patch('subprocess.run',return_value=Fake(0,
                'ActiveState=active\nControlGroup=/user.slice/active\nLoadState=loaded\n')):
            result=self.b.reconcile_stranded()
        self.assertEqual(result['still_stranded'],[job])
        self.assertEqual(self.b.inspect(self.submit,job)['status'],'stranded')

    def test_reconcile_verified_inactive_scope_marks_failed_not_requeued(self):
        job=self.enqueue('report')
        self.b.claim(self.worker)
        with self.b._db() as db:
            db.execute('UPDATE jobs SET status="stranded" WHERE id=?',(job,))
        class Fake:
            returncode=0
            stdout='ActiveState=inactive\nControlGroup=\nLoadState=loaded\n'
        with patch('subprocess.run',return_value=Fake()):
            result=self.b.reconcile_stranded()
        self.assertEqual(result['verified_failed'],[job])
        self.assertFalse(result['automatic_retry'])
        self.assertEqual(self.b.inspect(self.submit,job)['status'],'failed')
        self.assertEqual(self.b.resource_status()['reserved_ram_mb'],0)

    def test_watchdog_emergency_only_under_critical_pressure(self):
        self.b.resource_gate=rg.ResourceGate(memory_reader=lambda:12000,
            gpu_reader=lambda:{},policy_reader=lambda:{'temperature_c':96},
            runner_reader=lambda:{'reserved_ram_mb':0,'active_runners':0,'reserved_cpus':0})
        with patch.object(self.b,'emergency_stop',return_value={'paused':True}) as emergency,\
             patch.object(self.b,'reconcile_stranded',return_value={}) as reconcile:
            result=self.b.watchdog_once()
        self.assertEqual(result['critical'],'critical_thermal')
        emergency.assert_called_once_with(confirm=True)
        reconcile.assert_called_once_with()

    def test_emergency_pauses_and_never_releases_leases(self):
        job=self.enqueue('report')
        self.b.claim(self.worker)
        class Fake:
            def __init__(self,returncode):self.returncode=returncode
        with patch('subprocess.run',side_effect=[Fake(0),Fake(3)]) as stop:
            result=self.b.emergency_stop(confirm=True)
        self.assertEqual(result['unconfirmed'],0)
        self.assertTrue(result['paused'])
        self.assertEqual(result['stop_results'][0]['job_id'],job)
        self.assertEqual(self.b.inspect(self.submit,job)['status'],'stranded')
        self.enqueue('build')
        self.assertEqual(self.b.claim(self.worker)['reason'],'admissions_paused')
        self.assertEqual(stop.call_count,2)

class NativeRunnerStartTests(unittest.TestCase):
    def test_blocks_start_if_app_and_ci_reserved_memory_too_large(self):
        fake={'policy':{'paused':False,'max_parallel':3},
              'reserved_native_runner_ram_mb':4096,'reserved_ram_mb':2048,
              'capacity':{'min_free_ram_mb':4096}}
        with patch('app_runtime.Broker') as broker,\
             patch('resource_guard.memory_available_mb',return_value=10000):
            broker.return_value.resource_status.return_value=fake
            with self.assertRaises(RuntimeError):
                ci.require_start_capacity()
    def test_blocks_start_on_paused_or_unknown_metrics(self):
        fake={'policy':{'paused':True,'max_parallel':0},
              'reserved_native_runner_ram_mb':0,'reserved_ram_mb':0,
              'capacity':{'min_free_ram_mb':4096}}
        with patch('app_runtime.Broker') as broker:
            broker.return_value.resource_status.return_value=fake
            with self.assertRaises(RuntimeError):ci.require_start_capacity()
    def test_allows_start_with_sufficient_remaining_capacity(self):
        fake={'policy':{'paused':False,'max_parallel':3},
              'reserved_native_runner_ram_mb':2048,'reserved_ram_mb':1024,
              'capacity':{'min_free_ram_mb':4096}}
        with patch('app_runtime.Broker') as broker,\
             patch('resource_guard.memory_available_mb',return_value=14000):
            broker.return_value.resource_status.return_value=fake
            self.assertTrue(ci.require_start_capacity()['admitted'])

from tests.test_web_console import DashboardHTTPTests

class WorkloadDashboardHTTPTests(DashboardHTTPTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.console.apps.resource_gate=rg.ResourceGate(
            memory_reader=lambda:15000,gpu_reader=lambda:{},
            policy_reader=lambda:healthy(),
            runner_reader=lambda:{'reserved_ram_mb':0,'active_runners':0,'reserved_cpus':0})
        cls.console.apps.register('safe-control-app',['prepare'],confirm=True)
    def test_management_mode_and_queue_access(self):
        cookie=self.session()
        code,inventory,_=self.request('GET','/api/apps/inventory',cookie=cookie)
        self.assertEqual(code,200)
        self.assertIn('policy',inventory['resources'])
        code,queue,_=self.request('GET','/api/apps/queue',cookie=cookie)
        self.assertEqual(code,200)
        self.assertIn('jobs',queue)
        code,changed,_=self.request('POST','/api/apps/control',{
            'action':'pause','confirm':True},cookie=cookie)
        self.assertEqual(code,200)
        self.assertTrue(changed['paused'])
        code,changed,_=self.request('POST','/api/apps/control',{
            'action':'resume','confirm':True},cookie=cookie)
        self.assertEqual(code,200)
        self.assertFalse(changed['paused'])
    def test_control_refuses_unconfirmed_and_bad_modes(self):
        cookie=self.session()
        code,_,_=self.request('POST','/api/apps/control',{'action':'pause'},cookie=cookie)
        self.assertEqual(code,400)
        code,_,_=self.request('POST','/api/apps/control',
                             {'action':'arbitrary_command','confirm':True},cookie=cookie)
        self.assertEqual(code,400)
