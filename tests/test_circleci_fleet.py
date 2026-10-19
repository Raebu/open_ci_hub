"""CircleCI native machine migration must preserve steps and block unsafe execution."""
import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import circleci_fleet as cf

SOURCE = """version: 2.1
jobs:
  test:
    machine: true
    steps:
      - checkout
      - run: python3 -m unittest discover -s tests
workflows:
  verify:
    jobs:
      - test:
          filters:
            branches:
              only: main
"""

class CircleCiMigrationTests(unittest.TestCase):
    def test_machine_job_preserves_steps(self):
        new,n=cf.migrate(SOURCE,'main','team/local')
        self.assertEqual(n,1)
        self.assertIn('    resource_class: team/local',new)
        self.assertIn('python3 -m unittest discover -s tests',new)
        self.assertEqual(new.replace('    resource_class: team/local\n',''),SOURCE)

    def test_rejects_unfiltered_broad_runners(self):
        variants=[
            SOURCE.replace('              only: main','              only: development'),
            SOURCE.replace('              only: main','              only: [main, development]'),
            SOURCE.replace('      - test:\n          filters:\n            branches:\n              only: main','      - test'),
            SOURCE.replace('    machine: true','    docker:\n      - image: cimg/python:3.12'),
            SOURCE.replace('    machine: true','    machine: true\n    resource_class: medium'),
            SOURCE.replace('    machine: true','    executor: docker'),
            SOURCE.replace('workflows:', 'orbs:\n  node: circleci/node@5\nworkflows:'),
            SOURCE.replace('workflows:', 'setup: true\nworkflows:'),
            SOURCE.replace('workflows:', 'workflows:\n  schedule:\n    triggers:\n      - schedule:\n          cron: "0 1 * * *"\n'),
        ]
        for case in variants:
            with self.subTest(text=case),self.assertRaises(RuntimeError):
                cf.migrate(case,'main','team/local')

    def test_invalid_resource_class(self):
        with self.assertRaises(ValueError):
            cf.migrate(SOURCE,'main','bad;evil')

    def test_assessment_does_not_rewrite_missing_config(self):
        with patch('circleci_fleet._json_object',return_value={'truncated':False,'tree':[]}):
            p=cf.assess('Team/repo','main','team/local')
        self.assertEqual(p.state,'no-workflow')

    def test_assessment_uses_github_file_sha(self):
        with patch('circleci_fleet._json_object',return_value={
             'truncated':False,'tree':[{'path':'.circleci/config.yml'}]}), \
             patch('circleci_fleet.fleet._file',return_value=(SOURCE,'a'*40)):
            p=cf.assess('Team/repo','main','team/local')
        self.assertEqual(p.state,'review-ready')
        self.assertEqual(p.sha,'a'*40)

class CircleCiFleetSafetyTests(unittest.TestCase):
    def test_owner_scope_requires_approval_and_class(self):
        with tempfile.TemporaryDirectory() as td,patch.object(cf,'POLICY',Path(td)/'.local'/'circleci.json'):
            with self.assertRaises(PermissionError):
                cf.authorize('Acme','team/local')
            cf.authorize('Acme','team/local',True)
            self.assertEqual(cf.resource_for('acme'),'team/local')
            self.assertEqual(cf.POLICY.stat().st_mode & 0o077,0)

    def test_no_runner_prevents_writes(self):
        p=cf.Plan('Acme/app','main','review-ready','ok',SOURCE,'a'*40,1,'team/local')
        with patch('circleci_fleet.resource_for',return_value='team/local'),\
             patch('circleci_fleet.has_pending_pr',return_value=False),\
             patch('circleci_fleet.check_readiness',return_value={'verified':False}),\
             patch('circleci_fleet._json_object') as write:
            with self.assertRaisesRegex(RuntimeError,'unverified'):
                cf.create_pr(p)
            write.assert_not_called()

    def test_existing_fleet_pr_prevents_writes(self):
        p=cf.Plan('Acme/app','main','review-ready','ok',SOURCE,'a'*40,1,'team/local')
        with patch('circleci_fleet.resource_for',return_value='team/local'),\
             patch('circleci_fleet.has_pending_pr',return_value=True),\
             patch('circleci_fleet._json_object') as write:
            with self.assertRaisesRegex(RuntimeError,'already'):
                cf.create_pr(p)
            write.assert_not_called()

    def test_review_pr_preserves_original_sha_and_branch(self):
        p=cf.Plan('Acme/app','main','review-ready','ok',SOURCE,'a'*40,1,'team/local')
        with patch('circleci_fleet.resource_for',return_value='team/local'),\
             patch('circleci_fleet.has_pending_pr',return_value=False),\
             patch('circleci_fleet.check_readiness',return_value={'verified':True}),\
             patch('circleci_fleet.assess',return_value=p),\
             patch('circleci_fleet.secrets.token_hex',return_value='1212'),\
             patch('circleci_fleet._json_object',side_effect=[
                {'object':{'sha':'b'*40}}, {}, {},
                {'html_url':'https://github.com/Acme/app/pull/3'}]) as api:
            u=cf.create_pr(p)
        self.assertEqual(u,'https://github.com/Acme/app/pull/3')
        update=' '.join(api.call_args_list[2].args[0])
        self.assertIn('sha='+'a'*40,update)
        self.assertIn('branch=open-ci-hub/circleci-1212',update)

if __name__ == '__main__':
    unittest.main()
