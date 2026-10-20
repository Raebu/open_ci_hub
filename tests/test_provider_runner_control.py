"""Native provider runners must not evade owner-controlled resource admission."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import provider_runner_control as p

class ProviderGuardTests(unittest.TestCase):
    def cfg(self):
        return {
            'Memory':2*1024**3, 'MemorySwap':2*1024**3,
            'NanoCpus':1_500_000_000,'PidsLimit':256,
            'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges:true'],
            'RestartPolicy':{'Name':'no'},'Privileged':False,
            'NetworkMode':'open-ci-hub_default',
        }

    def docker(self,provider='github',cfg=None,state='exited',labels=None):
        cfg=cfg or self.cfg()
        label=labels or {'com.docker.compose.project':'open-ci-hub',
                         'com.docker.compose.service':provider}
        def run(args):
            self.assertEqual(args[0],'inspect')
            if '{{json .HostConfig}}' in args:return json.dumps(cfg)
            if '{{json .Config.Labels}}' in args:return json.dumps(label)
            if '{{.State.Status}}' in args:return state
            raise AssertionError('Unexpected Docker invocation: '+repr(args))
        return run

    def test_all_three_providers_can_be_inspected_without_start(self):
        for service in p.PROFILES:
            with self.subTest(service=service),patch('provider_runner_control.docker',
                     side_effect=self.docker(service)) as commands:
                result=p.inspect(service)
                self.assertEqual(result['state'],'exited')
                self.assertEqual(result['memory_mb'],2048)
                self.assertEqual(result['cpu_cores'],1.5)
                self.assertEqual(commands.call_count,3)

    def test_legacy_containers_cannot_restart_without_caps(self):
        for attr,val in [
            ('Memory',0),('MemorySwap',4*1024**3),('NanoCpus',0),
            ('PidsLimit',-1),('CapDrop',[]),('SecurityOpt',[]),
            ('Privileged',True),('RestartPolicy',{'Name':'unless-stopped'}),
            ('NetworkMode','host')
        ]:
            config=self.cfg();config[attr]=val
            with self.subTest(attr=attr),patch('provider_runner_control.docker',
                side_effect=self.docker(cfg=config)):
                with self.assertRaisesRegex(RuntimeError,'resource/security caps'):
                    p.inspect('github')

    def test_requires_exact_compose_labels(self):
        with patch('provider_runner_control.docker',
            side_effect=self.docker(labels={'com.docker.compose.project':'other',
                   'com.docker.compose.service':'github'})):
            with self.assertRaisesRegex(RuntimeError,'identity'):
                p.inspect('github')

    def test_owner_approval_is_required_and_no_container_created(self):
        with patch('provider_runner_control.allowlist',return_value={'approved':[]}):
            with patch('provider_runner_control.docker') as dock:
                with self.assertRaises(PermissionError):p.start('github')
            dock.assert_not_called()
        with self.assertRaises(PermissionError):
            p.approve('github')

    def test_failed_resource_capacity_never_starts(self):
        with patch('provider_runner_control.allowlist',return_value={'approved':['gitlab']}),\
            patch('provider_runner_control.inspect',return_value={
                'state':'exited','memory_mb':2048,'provider':'gitlab','container':p.PROFILES['gitlab']}),\
            patch('provider_runner_control.require_start_capacity',side_effect=RuntimeError('low disk')),\
            patch('provider_runner_control.docker') as dock:
            with self.assertRaisesRegex(RuntimeError,'low disk'):
                p.start('gitlab')
            dock.assert_not_called()

    def test_approved_bounded_exited_container_only_started_after_gate(self):
        calls=[]
        with patch('provider_runner_control.allowlist',return_value={'approved':['circleci']}),\
            patch('provider_runner_control.inspect',return_value={
                'state':'exited','memory_mb':2048,'provider':'circleci','container':p.PROFILES['circleci']}),\
            patch('provider_runner_control.require_start_capacity',
                  side_effect=lambda size:calls.append(('admission',size)) or {'admitted':True}),\
            patch('provider_runner_control.docker',
                  side_effect=lambda args:calls.append(('docker',args)) or ''):
            result=p.start('circleci')
        self.assertEqual(result['status'],'started')
        self.assertEqual(calls[0],('admission',2048))
        self.assertEqual(calls[1],('docker',['start',p.PROFILES['circleci']]))

    def test_running_runner_untouched(self):
        with patch('provider_runner_control.allowlist',return_value={'approved':['github']}),\
            patch('provider_runner_control.inspect',return_value={
                'state':'running','memory_mb':2048,'provider':'github','container':p.PROFILES['github']}),\
            patch('provider_runner_control.require_start_capacity') as gate,\
            patch('provider_runner_control.docker') as dock:
            self.assertEqual(p.start('github')['status'],'running')
        gate.assert_not_called()
        dock.assert_not_called()

    def test_schedule_defers_unsafe_runners_without_stopping_active_jobs(self):
        with patch('provider_runner_control.allowlist',return_value={'approved':['github','gitlab']}),\
            patch('provider_runner_control.start',side_effect=[RuntimeError('low disk'),{
                'status':'running','provider':'gitlab'}]):
            result=p.schedule()
        self.assertEqual(result['providers']['github']['status'],'deferred')
        self.assertEqual(result['providers']['gitlab']['status'],'running')

    def test_compose_prevents_restart_policy_bypass(self):
        config=Path(__file__).resolve().parent.parent.joinpath('docker-compose.yml').read_text()
        self.assertNotIn('unless-stopped',config)
        self.assertEqual(config.count('restart: "no"'),3)

if __name__=='__main__':
    unittest.main()
