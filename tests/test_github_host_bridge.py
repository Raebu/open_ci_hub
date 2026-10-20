"""Host GitHub command bridge: no GitHub request grants shell access on its own."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import github_host_bridge as bridge


def issue(number=17, author='Raebu', body=None):
    task = {
        'schema': bridge.SCHEMA,
        'description': 'Create harmless local test document',
        'argv': ['/usr/bin/python3', '-c', 'print("hello")'],
        'cwd': '/tmp',
        'timeout': 30,
    }
    return {
        'number': number, 'title': '[hub-task] test file',
        'body': json.dumps(task) if body is None else body,
        'state': 'open', 'user': {'login': author},
        'created_at': datetime.now(timezone.utc).isoformat()
    }


CFG = {'repo': bridge.TRUSTED_REPO, 'sender': 'Raebu',
       'repo_id': bridge.TRUSTED_REPO_ID, 'configured_at': int(time.time()) - 120}


class BridgeTests(unittest.TestCase):
    def test_rejects_untrusted_issue_and_arbitrary_schema(self):
        for mutation in (
            {'user': {'login': 'OtherUser'}},
            {'state': 'closed'},
            {'pull_request': {'url': 'some-pr'}},
            {'title': 'Not a host task'},
        ):
            row = {**issue(), **mutation}
            with self.subTest(mutation=mutation), self.assertRaises(
                (ValueError, PermissionError)
            ):
                bridge.validate_task(row, CFG)
        data = json.loads(issue()['body'])
        data['unexpected'] = 'ignore safeguards'
        with self.assertRaises(ValueError):
            bridge.validate_task(issue(body=json.dumps(data)), CFG)
        data.pop('unexpected')
        data['argv'] = ['bash', '-c', 'do something']
        with self.assertRaises(ValueError):
            bridge.validate_task(issue(body=json.dumps(data)), CFG)

    def test_digest_binds_request_and_rejects_edits(self):
        original = bridge.validate_task(issue(), CFG)
        changed = issue()
        changed['body'] = changed['body'].replace('hello', 'goodbye')
        self.assertNotEqual(original['digest'],
                            bridge.validate_task(changed, CFG)['digest'])
        self.assertEqual(len(original['digest']), 64)

    @patch('github_host_bridge.require_user')
    def test_config_requires_private_owner_repo(self, user):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(bridge, 'CONFIG', Path(temp)/'private.json'):
                with patch('github_host_bridge.gh', return_value={
                    'private': False, 'full_name': CFG['repo'],
                    'id': CFG['repo_id'], 'owner': {'login': 'Raebu'}}):
                    with self.assertRaises(PermissionError):
                        bridge.configure(CFG['repo'], 'Raebu', confirm=True)
                with patch('github_host_bridge.gh', return_value={
                    'private': True, 'full_name': CFG['repo'],
                    'id': CFG['repo_id'], 'owner': {'login': 'Raebu'}}):
                    result = bridge.configure(CFG['repo'], 'Raebu', confirm=True)
                    self.assertEqual(result['repo'], CFG['repo'])
                    self.assertEqual(result['repo_id'], CFG['repo_id'])
                    self.assertGreater(result['configured_at'], 0)
                    self.assertEqual((Path(temp)/'private.json').stat().st_mode & 0o077, 0)

    def test_control_repo_is_checked_again_before_execution(self):
        with patch('github_host_bridge.gh', return_value={
            'id': CFG['repo_id'], 'private': False, 'full_name': CFG['repo'],
            'owner': {'login': CFG['sender']}
        }):
            with self.assertRaises(PermissionError):
                bridge.verify_control_repo(CFG)
        with patch('github_host_bridge.gh', return_value={
            'id': CFG['repo_id'], 'private': True, 'full_name': CFG['repo'],
            'owner': {'login': CFG['sender']}
        }):
            self.assertTrue(bridge.verify_control_repo(CFG))

    @patch('github_host_bridge.require_user')
    def test_dedicated_control_repo_only(self, unused):
        with patch('github_host_bridge.gh') as api:
            with self.assertRaises(PermissionError):
                bridge.configure('Raebu/other-private-repo', 'Raebu', confirm=True)
            api.assert_not_called()
        with patch('github_host_bridge.gh', return_value={
            'id': 999, 'private': True,
            'full_name': CFG['repo'], 'owner': {'login': CFG['sender']}
        }):
            with self.assertRaises(PermissionError):
                bridge.configure(CFG['repo'], 'Raebu', confirm=True)

    @patch('github_host_bridge.require_user')
    def test_explicit_approval_digest_and_no_replay(self, user):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(bridge, 'JOBS', Path(temp)), \
                 patch('github_host_bridge.config', return_value=CFG), \
                 patch('github_host_bridge.verify_control_repo'), \
                 patch('github_host_bridge.get_issue', return_value=bridge.validate_task(issue(), CFG)):
                digest = bridge.validate_task(issue(), CFG)['digest']
                with self.assertRaises(PermissionError):
                    bridge.approve(17, digest)
                with self.assertRaises(PermissionError):
                    bridge.approve(17, '0'*64, confirm=True)
                result = bridge.approve(17, digest, confirm=True)
                self.assertEqual(result['status'], 'approved')
                self.assertEqual((Path(temp)/'17.json').stat().st_mode & 0o077, 0)
                with self.assertRaises(RuntimeError):
                    bridge.approve(17, digest, confirm=True)

    @patch('github_host_bridge.require_user')
    def test_approved_request_runs_once_and_only_reports_summary(self, user):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/'17.json'
            request = bridge.validate_task(issue(), CFG)
            target.write_text(json.dumps({
                'issue': 17, 'digest': request['digest'],
                'repo_id': CFG['repo_id'],
                'status': 'approved', 'approved_at': int(time.time())}))
            target.chmod(0o600)
            calls = []
            def stub(task, number):
                calls.append((task, number))
                return {'status': 'completed', 'exit_code': 0,
                        'output_sha256': 'f'*64, 'output_preview': 'secret-local-output'}
            def github(*args, **kwargs):
                self.assertEqual(kwargs['method'], 'POST')
                self.assertNotIn('secret-local-output', kwargs['fields']['body'])
                return {'id': 1}
            with patch.object(bridge, 'JOBS', Path(temp)), \
                 patch('github_host_bridge.config', return_value=CFG), \
                 patch('github_host_bridge.verify_control_repo'), \
                 patch('github_host_bridge.get_issue', return_value=request), \
                 patch('github_host_bridge.safe_capacity'), \
                 patch('github_host_bridge.stage_standing_requests', return_value=0), \
                 patch('github_host_bridge.gh', side_effect=github):
                first = bridge.run_once(stub)
                second = bridge.run_once(stub)
                self.assertEqual(first['status'], 'completed')
                self.assertEqual(second['status'], 'idle')
            self.assertEqual(len(calls), 1)
            self.assertTrue(json.loads(target.read_text())['reported'])

    @patch('github_host_bridge.require_user')
    def test_changed_issue_never_executes(self, user):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'17.json'
            path.write_text(json.dumps({'issue': 17, 'digest': 'a'*64,
                            'repo_id': CFG['repo_id'],
                            'approved_at': int(time.time()), 'status': 'approved'}))
            path.chmod(0o600)
            with patch.object(bridge, 'JOBS', Path(temp)), \
                 patch('github_host_bridge.config', return_value=CFG), \
                 patch('github_host_bridge.verify_control_repo'), \
                 patch('github_host_bridge.get_issue', return_value=bridge.validate_task(issue(), CFG)), \
                 patch('github_host_bridge.safe_capacity') as gate, \
                 patch('github_host_bridge.stage_standing_requests', return_value=0):
                self.assertEqual(bridge.run_once()['status'], 'idle')
                gate.assert_not_called()
            self.assertEqual(json.loads(path.read_text())['status'], 'rejected_changed')

    def test_host_capacity_reserves_existing_native_runner_ram(self):
        from unittest.mock import MagicMock
        resources = {
            'memory_available_mb': 9300,
            'reserved_native_runner_ram_mb': 4096,
            'reserved_ram_mb': 0,
            'capacity': {'min_free_ram_mb': 4096},
        }
        broker = MagicMock()
        broker.resource_status.return_value = resources
        with patch('machine_policy.sample', return_value={'temperature_c': 65}), \
             patch('machine_policy.admission', return_value={'max_parallel': 1}), \
             patch('app_runtime.Broker', return_value=broker):
            self.assertTrue(bridge.safe_capacity())
            resources['memory_available_mb'] = 8900
            with self.assertRaisesRegex(RuntimeError, 'protecting desktop'):
                bridge.safe_capacity()
            resources['reserved_native_runner_ram_mb'] = None
            with self.assertRaisesRegex(RuntimeError, 'telemetry'):
                bridge.safe_capacity()
            resources['reserved_native_runner_ram_mb'] = 4096
            resources['memory_available_mb'] = 9300
            resources['policy'] = {'paused': True, 'max_parallel': 1}
            with self.assertRaisesRegex(RuntimeError, 'admissions paused'):
                bridge.safe_capacity()

    def test_host_capacity_blocks_unknown_thermal_readings(self):
        with patch('machine_policy.sample', return_value={'temperature_c': None}):
            with self.assertRaisesRegex(RuntimeError, 'thermal sensor unavailable'):
                bridge.safe_capacity()

    @patch('github_host_bridge.require_user')
    def test_unsafe_pressure_defers_without_consuming_approval(self, user):
        with tempfile.TemporaryDirectory() as temp:
            request=bridge.validate_task(issue(), CFG)
            path=Path(temp)/'17.json'
            path.write_text(json.dumps({'issue': 17, 'digest': request['digest'],
                            'repo_id': CFG['repo_id'],
                            'approved_at': int(time.time()), 'status': 'approved'}))
            path.chmod(0o600)
            with patch.object(bridge, 'JOBS', Path(temp)), \
                 patch('github_host_bridge.config', return_value=CFG), \
                 patch('github_host_bridge.verify_control_repo'), \
                 patch('github_host_bridge.get_issue', return_value=request), \
                 patch('github_host_bridge.safe_capacity', side_effect=RuntimeError('hot')), \
                 patch('github_host_bridge.stage_standing_requests', return_value=0), \
                 self.assertRaisesRegex(RuntimeError, 'hot'):
                bridge.run_once()
            self.assertEqual(json.loads(path.read_text())['status'], 'approved')


    def test_standing_document_is_valid_but_shell_is_not_standing_approved(self):
        task = {'schema': bridge.SCHEMA, 'description': 'Save document',
                'operation': 'create_document', 'filename': 'proposal.md',
                'content': 'Document contents', 'attestation': {'placeholder': True}}
        request = bridge.validate_task(issue(body=json.dumps(task)), CFG)
        self.assertTrue(bridge.is_routine(request))
        self.assertTrue(bridge.recent_after_configuration(request, CFG))
        for bad in ('../escape.md', 'subdir/file.md', 'notes.sh', '.hidden.md'):
            task['filename'] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                bridge.validate_task(issue(body=json.dumps(task)), CFG)
        self.assertFalse(bridge.is_routine(bridge.validate_task(issue(), CFG)))

    @patch('github_host_bridge.require_user')
    def test_standing_document_runs_once_without_manual_approval(self, unused):
        routine = {'schema': bridge.SCHEMA, 'description': 'Create test',
                   'operation': 'create_document', 'filename': 'auto-test.md',
                   'content': 'Authorised routine document', 'attestation': {'placeholder': True}}
        request = bridge.validate_task(issue(body=json.dumps(routine)), CFG)
        with tempfile.TemporaryDirectory() as directory:
            executor_calls = []
            document_calls = []
            def no_shell(task, number):
                executor_calls.append(number)
                raise AssertionError('Arbitrary shell must not run automatically')
            def write_document(task, number):
                document_calls.append(number)
                return {'status': 'completed', 'exit_code': 0,
                        'output_sha256': 'a' * 64}
            with patch.object(bridge, 'JOBS', Path(directory)), \
                 patch('github_host_bridge.config', return_value=CFG), \
                 patch('github_host_bridge.verify_control_repo'), \
                 patch('github_host_bridge.pending', return_value=[request]), \
                 patch('github_host_bridge.get_issue', return_value=request), \
                 patch('github_host_bridge.safe_capacity'), \
                 patch('github_host_bridge.gh', return_value={'id': 1}), \
                 patch('request_attestation.verify_attestation',
                       return_value={'jti':'abcDEF123abcDEF123abcDEF123',
                                     'subject':'authenticated-user'}):
                first = bridge.run_once(no_shell, write_document)
                second = bridge.run_once(no_shell, write_document)
            self.assertEqual(first['status'], 'completed')
            self.assertEqual(second['status'], 'idle')
            self.assertEqual(document_calls, [17])
            self.assertEqual(executor_calls, [])
            journal = json.loads((Path(directory) / '17.json').read_text())
            self.assertEqual(journal['authorisation'], 'standing_create_document')

    @patch('github_host_bridge.require_user')
    def test_unsigned_issues_are_never_automatically_staged(self, unused):
        routine = {'schema': bridge.SCHEMA, 'description': 'Create test',
                   'operation': 'create_document', 'filename': 'unsigned.md',
                   'content': 'No signature', 'attestation': {'placeholder': True}}
        request = bridge.validate_task(issue(body=json.dumps(routine)), CFG)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(bridge, 'JOBS', Path(directory)), \
                 patch('github_host_bridge.pending', return_value=[request]):
                self.assertEqual(bridge.stage_standing_requests(CFG), 0)
                self.assertFalse((Path(directory) / '17.json').exists())

    @patch('github_host_bridge.require_user')
    def test_legacy_shell_never_staged_for_automatic_execution(self, unused):
        request = bridge.validate_task(issue(), CFG)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(bridge, 'JOBS', Path(directory)), \
                 patch('github_host_bridge.pending', return_value=[request]):
                self.assertEqual(bridge.stage_standing_requests(CFG), 0)
                self.assertFalse((Path(directory) / '17.json').exists())

    @unittest.skipIf(not hasattr(os, 'geteuid') or os.geteuid() == 0,
                     'Host output filesystem test must run non-root')
    def test_native_document_creation_is_create_only(self):
        doc = {'schema': bridge.SCHEMA, 'description': 'create test',
               'operation': 'create_document', 'filename': 'safe.md',
               'content': 'hello local world', 'attestation': {'placeholder': True}}
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            (home / 'Documents').mkdir()
            with patch('github_host_bridge.Path.home', return_value=home):
                actual = bridge.create_document(doc, 17)
                self.assertEqual(actual['status'], 'completed')
                output = home / 'Documents' / 'OpenCIHubTasks' / 'safe.md'
                self.assertEqual(output.read_text(), doc['content'])
                self.assertEqual(output.stat().st_mode & 0o077, 0)
                with self.assertRaises(FileExistsError):
                    bridge.create_document(doc, 18)
            doc['filename'] = '../outside.md'
            with self.assertRaises((ValueError, PermissionError)):
                bridge.create_document(doc, 19)


if __name__ == '__main__':
    unittest.main()
