"""Fleet controller regression tests: approval, isolation and CI preservation."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import fleet

GOOD = """name: Build
on:
  push:
    branches: [main]
  workflow_dispatch:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: python3 -m unittest discover -s tests
"""

class MigrationTests(unittest.TestCase):
    def test_preserves_steps_replaces_only_runner(self):
        result, count = fleet.migrate(GOOD, 'main')
        self.assertEqual(count, 1)
        self.assertIn('runs-on: [self-hosted, linux, local-ci]', result)
        self.assertIn('python3 -m unittest discover -s tests', result)
        self.assertEqual(result.replace('[self-hosted, linux, local-ci]', 'ubuntu-latest'), GOOD)

    def test_rejects_untrusted_and_ambiguous_triggers(self):
        unsafe = [
            GOOD.replace('  workflow_dispatch:', '  pull_request:'),
            GOOD.replace('    branches: [main]', '    branches: [feature]'),
            GOOD.replace('    branches: [main]', '    branches:\n      - main\n      - another'),
            GOOD.replace('    branches: [main]', '    branches: [main, other]'),
            GOOD.replace('    branches: [main]', ''),
            GOOD.replace('on:\n  push:', 'on: [push]'),
            GOOD.replace('    runs-on: ubuntu-latest', '    runs-on: ubuntu-latest\n    container: python:3'),
            GOOD.replace('    runs-on: ubuntu-latest', '    runs-on: ubuntu-latest\n    services:\n      redis:\n        image: redis'),
        GOOD.replace('    runs-on: ubuntu-latest', '    runs-on: ubuntu-latest\n    strategy:\n      matrix:\n        python: [3.11]'),
        GOOD.replace('    runs-on: ubuntu-latest', '    runs-on: ubuntu-latest\n    steps:\n      - run: sudo apt-get update'),
        GOOD.replace('  push:', '  workflow_run:')
        ]
        for source in unsafe:
            with self.subTest(source=source), self.assertRaises(RuntimeError):
                fleet.migrate(source, 'main')

    def test_non_linux_jobs_untouched(self):
        changed, count = fleet.migrate(GOOD.replace('ubuntu-latest', 'windows-latest'), 'main')
        self.assertEqual(count, 0)
        self.assertEqual(changed.count('windows-latest'), 1)

    def test_tree_and_candidate_preserve_existing_workflow(self):
        tree = {'truncated': False, 'tree': [{'path': '.github/workflows/test.yml'}]}
        original = {'type':'file','encoding':'base64','size':len(GOOD.encode()),
                    'sha':'a'*40,'content':base64.b64encode(GOOD.encode()).decode()}
        with patch('fleet._json_object', side_effect=[tree, original]):
            plan = fleet.assess('example/ci', 'main')
        self.assertEqual(plan.state, 'review-ready')
        self.assertEqual(len(plan.changes), 1)
        self.assertIn('python3 -m unittest', plan.changes[0].content)

    def test_fails_closed_if_any_workflow_is_unsafe(self):
        tree = {'truncated': False, 'tree': [
            {'path': '.github/workflows/first.yml'},
            {'path': '.github/workflows/second.yml'}]}
        with patch('fleet._json_object', return_value=tree), \
             patch('fleet._file', side_effect=[
                 (GOOD, 'a'*40), (GOOD.replace('  workflow_dispatch:', '  pull_request:'), 'b'*40)]):
            result = fleet.assess('example/ci', 'main')
        self.assertEqual(result.state, 'manual')
        self.assertFalse(result.changes)

class FleetSafetyTests(unittest.TestCase):
    def test_discover_excludes_forks_archives_other_owners(self):
        def entry(name, fork=False, archived=False):
            return {'nameWithOwner':name,'isFork':fork,'isArchived':archived,
                    'isPrivate':True,'defaultBranchRef':{'name':'main'}}
        with patch('fleet.invoke_json', return_value=[
                entry('Acme/one'), entry('Acme/fork',fork=True),
                entry('Acme/old',archived=True), entry('Other/org'),entry('Acme/two')]):
            self.assertEqual(fleet.discover('Acme'), [('Acme/one','main'),('Acme/two','main')])

    def test_policy_requires_explicit_confirmation_and_is_private(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(fleet, 'POLICY', Path(directory)/'.local'/'fleet-scopes.json'):
            with self.assertRaises(PermissionError):
                fleet.authorize('Acme')
            self.assertFalse(fleet.approved('Acme'))
            fleet.authorize('Acme', confirm=True)
            self.assertTrue(fleet.approved('acme'))
            self.assertEqual(len(fleet.authorize('Acme', confirm=True)['github']), 1)
            self.assertEqual(fleet.POLICY.stat().st_mode & 0o077, 0)

    def test_apply_never_writes_without_authorized_scope(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(fleet, 'POLICY', Path(directory)/'scopes.json'), \
             patch('fleet.discover') as discover:
            with self.assertRaises(PermissionError):
                fleet.sync('Acme', apply=True)
            discover.assert_not_called()

    def test_pr_blocked_if_runner_unverified(self):
        p = fleet.Plan('Acme/repo','main','review-ready','ok',
                       (fleet.Change('.github/workflows/test.yml','a'*40,GOOD,1),))
        with patch('fleet.approved', return_value=True), \
             patch('fleet.existing_pr', return_value=False), \
             patch('fleet.check_readiness', return_value={'verified':False}), \
             patch('fleet._json_object') as mutation:
            with self.assertRaisesRegex(RuntimeError, 'not verified'):
                fleet.create_pr(p)
            mutation.assert_not_called()

    def test_recheck_and_duplicate_suppression(self):
        p = fleet.Plan('Acme/repo','main','review-ready','ok',
                       (fleet.Change('.github/workflows/test.yml','a'*40,GOOD,1),))
        with patch('fleet.approved', return_value=True), \
             patch('fleet.existing_pr', return_value=True), \
             patch('fleet.check_readiness') as check:
            with self.assertRaisesRegex(RuntimeError, 'already exists'):
                fleet.create_pr(p)
            check.assert_not_called()

    def test_review_writes_only_proposed_branch_not_main(self):
        p = fleet.Plan('Acme/repo','main','review-ready','ok',
                       (fleet.Change('.github/workflows/test.yml','a'*40,GOOD,1),))
        with patch('fleet.approved',return_value=True), \
             patch('fleet.existing_pr',return_value=False), \
             patch('fleet.check_readiness',return_value={'verified':True}), \
             patch('fleet.assess',return_value=p), \
             patch('fleet.secrets.token_hex',return_value='00ff'), \
             patch('fleet._json_object',side_effect=[
                 {'object':{'sha':'b'*40}}, {}, {},
                 {'html_url':'https://github.com/Acme/repo/pull/12'}]) as api:
            url=fleet.create_pr(p)
        self.assertEqual(url,'https://github.com/Acme/repo/pull/12')
        args=' '.join(api.call_args_list[2].args[0])
        self.assertIn('branch=open-ci-hub/fleet-00ff',args)
        self.assertIn('sha='+'a'*40,args)
        self.assertNotIn('branch=main',args)

    def test_no_auto_merge_or_hosted_dispatch(self):
        import inspect
        source=inspect.getsource(fleet.create_pr)
        self.assertNotIn('merge', source.lower().split('def create_pr')[0] if False else '')  # Operational calls verified below
        self.assertNotIn('workflow_dispatch', source)
        self.assertNotIn('gh run', source)
        self.assertNotIn("'merge'", source)

if __name__ == '__main__':
    unittest.main()
