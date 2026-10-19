"""Personal GitHub native runner pool must preserve registrations and cap resources."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import runner_pool as pool
from providers import Registration

WORKFLOW = """name: Trusted main
on:
  push:
    branches: [main]
jobs:
  build:
    runs-on: [self-hosted, linux, local-ci]
    steps:
      - run: echo hello
"""

class RunnerPoolTests(unittest.TestCase):
    def test_names_are_stable_and_repo_specific(self):
        one = pool.worker_names('Owner/alpha')
        self.assertEqual(one,pool.worker_names('owner/ALPHA'))
        self.assertNotEqual(one,pool.worker_names('Owner/beta'))
        self.assertLessEqual(len(one[0]),64)
        with self.assertRaises(ValueError):
            pool.worker_names('Owner/sub/alpha')

    def test_scope_explicit_and_private(self):
        with tempfile.TemporaryDirectory() as td,patch.object(pool,'POLICY',Path(td)/'.local'/'scope.json'):
            with self.assertRaises(PermissionError):
                pool.authorize('Raebu')
            pool.authorize('Raebu',True)
            self.assertTrue(pool.approved('raebu'))
            self.assertEqual(pool.POLICY.stat().st_mode & 0o077,0)

    def test_blocks_unsafe_and_ambiguous_workflows(self):
        values=[WORKFLOW,
                WORKFLOW.replace('  push:','  pull_request:'),
                WORKFLOW.replace('[self-hosted, linux, local-ci]','ubuntu-latest'),
                WORKFLOW.replace('  push:','  push:\n    branches: [main,other]'),
                WORKFLOW.replace('    runs-on: [self-hosted, linux, local-ci]',
                                 '    uses: company/action@main')]
        tree={'truncated':False,'tree':[{'path':'.github/workflows/main.yml'}]}
        expected=[True,False,False,False,False]
        for text,okay in zip(values,expected):
            with self.subTest(allowed=okay), \
                 patch('runner_pool.fleet._json_object',return_value=tree), \
                 patch('runner_pool.fleet._file',return_value=(text,'a'*40)):
                self.assertEqual(pool._trusted_workflow('Raebu/app','main'),okay)

    def test_register_never_mints_token_without_approval(self):
        with patch('runner_pool.approved',return_value=False), \
             patch('runner_pool._docker') as docker, \
             patch('runner_pool.prepare_target') as prepare:
            with self.assertRaises(PermissionError):
                pool.register('Raebu/app','main')
            docker.assert_not_called()
            prepare.assert_not_called()

    def test_registration_token_not_embedded_in_docker_argv(self):
        calls=[]
        def docker(args, **opts):
            calls.append((args,opts))
            if args[0]=='volume' and args[1]=='inspect':
                return '',1
            return '',0
        with patch('runner_pool.approved',return_value=True), \
             patch('runner_pool._trusted_workflow',return_value=True), \
             patch('runner_pool._state',return_value={}), \
             patch('runner_pool._docker',side_effect=docker), \
             patch('runner_pool._configured',return_value=True), \
             patch('runner_pool.prepare_target',return_value=Registration(
                 name='pool',target='https://github.com/Raebu/app',
                 token='HIGHLY_SECRET_TEST',metadata={})), \
             patch('runner_pool.secure_write'):
            self.assertEqual(pool.register('Raebu/app','main'),'registered')
        args=' '.join(' '.join(x) for x,opts in calls)
        self.assertNotIn('HIGHLY_SECRET_TEST',args)
        self.assertIn('-e GH_TOKEN',args)
        self.assertIn('--cap-drop ALL',args)
        self.assertNotIn('/var/run/docker.sock',args)

    def test_refuses_unknown_preexisting_state_volume(self):
        def docker(args,**kwargs):
            if args[0]=='volume' and args[1]=='inspect':
                return 'exists',0
            return '',0
        with patch('runner_pool.approved',return_value=True), \
             patch('runner_pool._trusted_workflow',return_value=True), \
             patch('runner_pool._state',return_value={}), \
             patch('runner_pool._docker',side_effect=docker), \
             patch('runner_pool.prepare_target') as prepare:
            with self.assertRaisesRegex(RuntimeError,'already exists'):
                pool.register('Raebu/app','main')
            prepare.assert_not_called()

    def test_capacity_blocks_additional_running_workers(self):
        name,volume=pool.worker_names('Raebu/app')
        with patch('runner_pool._state',return_value={
                  'Raebu/app':{'container':name,'volume':volume,'branch':'main'}}), \
             patch('runner_pool._existing_container',return_value=None), \
             patch('runner_pool._configured',return_value=True), \
             patch('runner_pool._active',return_value={'first','second','third'}), \
             patch('runner_pool._docker') as docker:
            with self.assertRaisesRegex(RuntimeError,'capacity'):
                pool.start('Raebu/app')
            docker.assert_not_called()

    def test_scheduler_rotates_only_confirmed_idle_worker(self):
        a,va=pool.worker_names('Raebu/one')
        b,vb=pool.worker_names('Raebu/two')
        state={'Raebu/one':{'container':a,'volume':va,'branch':'main'},
               'Raebu/two':{'container':b,'volume':vb,'branch':'main'}}
        def queue(repo,status):
            return repo=='Raebu/two' if status=='queued' else False
        with patch('runner_pool._state',return_value=state), \
             patch('runner_pool._trusted_workflow',return_value=True), \
             patch('runner_pool._active',return_value={a}), \
             patch('runner_pool.MAX_ACTIVE',1), \
             patch('runner_pool.queued',side_effect=queue), \
             patch('runner_pool.start',return_value='started') as start, \
             patch('runner_pool.stop',return_value='stopped') as stop:
            result=pool.schedule()
        stop.assert_called_once_with('Raebu/one')
        start.assert_called_once_with('Raebu/two')
        self.assertEqual(result['active'],1)

    def test_scheduled_runner_stops_when_workflow_becomes_unsafe(self):
        repo='Raebu/untrusted'
        name,volume=pool.worker_names(repo)
        state={repo:{'container':name,'volume':volume,'branch':'main'}}
        with patch('runner_pool._state',return_value=state), \
             patch('runner_pool._active',return_value={name}), \
             patch('runner_pool._trusted_workflow',return_value=False), \
             patch('runner_pool.stop',return_value='stopped') as stop, \
             patch('runner_pool.queued') as queued:
            result=pool.schedule()
        stop.assert_called_once_with(repo)
        queued.assert_not_called()
        self.assertEqual(result['active'],0)

    def test_sync_pauses_existing_runner_on_unsafe_scope(self):
        repo='Raebu/untrusted'
        name,volume=pool.worker_names(repo)
        rows={repo:{'container':name,'volume':volume,'branch':'main'}}
        with patch('runner_pool.approved',return_value=True), \
             patch('runner_pool.fleet.discover',return_value=[(repo,'main')]), \
             patch('runner_pool._state',return_value=rows), \
             patch('runner_pool._trusted_workflow',return_value=False), \
             patch('runner_pool.stop',return_value='stopped') as stop, \
             patch('runner_pool.schedule',return_value={}) as schedule:
            out=pool.sync('Raebu',apply=True)
        stop.assert_called_once_with(repo)
        self.assertEqual(out[0]['status'],'paused-unsafe')
        schedule.assert_called_once()

    def test_stop_preserves_registered_volume(self):
        name,volume=pool.worker_names('Raebu/app')
        with patch('runner_pool._state',return_value={
                 'Raebu/app':{'container':name,'volume':volume,'branch':'main'}}), \
             patch('runner_pool._existing_container',return_value='running'), \
             patch('runner_pool._docker',return_value=('',0)) as docker:
            pool.stop('Raebu/app')
        self.assertEqual(docker.call_args.args[0][:2],['stop','--time'])

if __name__=='__main__':
    unittest.main()
