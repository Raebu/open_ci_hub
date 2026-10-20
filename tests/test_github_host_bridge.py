"""Host GitHub command bridge: no GitHub request grants shell access on its own."""
import json
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
        'state': 'open', 'user': {'login': author}
    }


CFG = {'repo': 'Raebu/PrivateControl', 'sender': 'Raebu', 'repo_id': 742}


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
                    self.assertEqual(result, CFG)
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
                 patch('github_host_bridge.safe_capacity') as gate:
                self.assertEqual(bridge.run_once()['status'], 'idle')
                gate.assert_not_called()
            self.assertEqual(json.loads(path.read_text())['status'], 'rejected_changed')

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
                 self.assertRaisesRegex(RuntimeError, 'hot'):
                bridge.run_once()
            self.assertEqual(json.loads(path.read_text())['status'], 'approved')


if __name__ == '__main__':
    unittest.main()
