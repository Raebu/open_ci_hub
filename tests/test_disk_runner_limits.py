"""Disk admission limits and fail-closed legacy native runner reactivation."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import machine_policy as mp
import runner_pool as pool

def disk(free_mb,pct):
    return {'min_free_mb':free_mb,'min_free_percent':pct,'mounts':[]}

class DiskPressureTests(unittest.TestCase):
    def test_low_free_space_blocks_new_work(self):
        x=mp.admission('balanced',{'disk':disk(1800,30)},3)
        self.assertEqual(x['max_parallel'],0)
        self.assertEqual(x['reason'],'critical_disk_pressure')
        x=mp.admission('balanced',{'disk':disk(10000,3)},3)
        self.assertEqual(x['max_parallel'],0)

    def test_moderate_storage_pressure_throttles_but_keeps_system_responsive(self):
        for observed in [disk(3000,60),disk(20000,8)]:
            x=mp.admission('balanced',{'disk':observed},3)
            self.assertEqual(x['max_parallel'],1)
            self.assertEqual(x['reason'],'disk_pressure_throttle')
        self.assertEqual(mp.admission('balanced',{'disk':disk(10000,35)},3)['max_parallel'],3)

    def test_missing_disk_sensor_stays_explicitly_unknown(self):
        with patch('machine_policy.os.statvfs',side_effect=OSError('no sensor')):
            self.assertIsNone(mp.disk_state())
        x=mp.admission('balanced',{'disk':None},3)
        self.assertEqual(x['max_parallel'],3)

    def test_project_checkout_partition_can_be_the_bottleneck(self):
        class Good:
            f_bavail=20000000
            f_frsize=4096
            f_blocks=100000000
        class Full:
            f_bavail=200000
            f_frsize=4096
            f_blocks=10000000
        def fake(path):
            return Full() if path==str(Path(mp.__file__).resolve().parent) else Good()
        with patch('machine_policy.os.statvfs',side_effect=fake):
            disk=mp.disk_state()
        self.assertEqual(disk['min_free_mb'],781)
        self.assertEqual(mp.admission('balanced',{'disk':disk},3)['max_parallel'],0)

    def test_disk_reports_true_free_capacity_of_root_and_home(self):
        class Fake:
            f_bavail=1000000
            f_frsize=4096
            f_blocks=10000000
        with patch('machine_policy.os.statvfs',return_value=Fake()):
            found=mp.disk_state()
        self.assertEqual(found['min_free_mb'],3906)
        self.assertEqual(found['min_free_percent'],10)
        self.assertTrue(found['mounts'])

class RunnerLimitsTests(unittest.TestCase):
    def valid_hostconfig(self):
        return {'Memory':2147483648,'MemorySwap':2147483648,'NanoCpus':2000000000,
                'PidsLimit':256,'CapDrop':['ALL'],
                'SecurityOpt':['no-new-privileges:true'],
                'RestartPolicy':{'Name':'no'}}

    def test_verified_hard_caps_allow_existing_runner(self):
        response=json.dumps(self.valid_hostconfig())
        with patch('runner_pool._docker',return_value=(response,0)):
            self.assertTrue(pool._legacy_container_safe(pool.PREFIX+'0123456789abcdef'))

    def test_legacy_runner_fails_closed_for_unbounded_swap_cpu_restart_or_privileges(self):
        for key,value in [
            ('Memory',0),('MemorySwap',4294967296),('NanoCpus',0),
            ('PidsLimit',-1),('CapDrop',[]),('SecurityOpt',[]),
            ('RestartPolicy',{'Name':'unless-stopped'})
        ]:
            cfg=self.valid_hostconfig();cfg[key]=value
            with self.subTest(key=key),patch('runner_pool._docker',return_value=(json.dumps(cfg),0)):
                self.assertFalse(pool._legacy_container_safe(pool.PREFIX+'0123456789abcdef'))
        with patch('runner_pool._docker',return_value=('',1)):
            self.assertFalse(pool._legacy_container_safe(pool.PREFIX+'0123456789abcdef'))

    def test_pool_never_auto_recreates_old_unbounded_container(self):
        repo='Raebu/legacy';name,volume=pool.worker_names(repo)
        calls=[]
        def docker(args,**kwargs):
            calls.append(args)
            if args[:2]==['inspect','--format']:
                return json.dumps({'Memory':0}),0
            return '',0
        with patch('runner_pool._state',return_value={repo:{
                'container':name,'volume':volume,'branch':'main'}}),\
             patch('runner_pool._existing_container',return_value='exited'),\
             patch('runner_pool._configured',return_value=True),\
             patch('runner_pool._active',return_value=set()),\
             patch('native_runner_observer.require_start_capacity',return_value={}),\
             patch('runner_pool._docker',side_effect=docker):
            with self.assertRaisesRegex(RuntimeError,'lacks verified'):
                pool.start(repo)
        self.assertFalse(any(cmd[0] in ('run','start','rm') for cmd in calls))

    def test_pool_new_container_has_bounded_swap_and_no_implicit_restart(self):
        repo='Raebu/fresh';name,volume=pool.worker_names(repo)
        calls=[]
        def docker(args,**kwargs):
            calls.append(args);return '',0
        with patch('runner_pool._state',return_value={repo:{
                'container':name,'volume':volume,'branch':'main'}}),\
             patch('runner_pool._existing_container',return_value=None),\
             patch('runner_pool._configured',return_value=True),\
             patch('runner_pool._active',return_value=set()),\
             patch('native_runner_observer.require_start_capacity',return_value={}),\
             patch('runner_pool._docker',side_effect=docker):
            self.assertEqual(pool.start(repo),'started')
        create=next(cmd for cmd in calls if cmd[0]=='run')
        self.assertEqual(create[create.index('--memory-swap')+1],'2g')
        self.assertEqual(create[create.index('--restart')+1],'no')
        self.assertIn('--pids-limit',create)

if __name__=='__main__':unittest.main()
