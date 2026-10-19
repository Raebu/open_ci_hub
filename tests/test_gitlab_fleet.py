"""Tests for GitLab group discovery and conservative native shell runner migration."""
import base64
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import gitlab_fleet as gf

PIPELINE = """workflow:
  rules:
    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'
stages:
  - test
test:
  stage: test
  script:
    - echo testing
    - python3 -m unittest discover -s tests
build:
  stage: test
  script:
    - npm test
"""

class GitLabMigrationTests(unittest.TestCase):
    def test_migration_preserves_all_original_scripts(self):
        new, count = gf.migrate(PIPELINE)
        self.assertEqual(count, 2)
        self.assertEqual(new.count('  tags: [local-ci]'), 2)
        self.assertIn('python3 -m unittest discover -s tests', new)
        self.assertIn('npm test', new)
        self.assertEqual(new.replace('  tags: [local-ci]\n',''), PIPELINE)

    def test_blocks_insecure_pipeline_and_executor_customization(self):
        examples = [
            PIPELINE.replace("    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'", "    - when: always"),
            PIPELINE.replace("    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'",
                             "    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'\n    - when: always"),
            PIPELINE.replace("    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'", "    - if: '$CI_PIPELINE_SOURCE == \"merge_request_event\"'"),
            PIPELINE.replace('  script:\n    - npm test','  tags: [hosted]\n  script:\n    - npm test'),
            PIPELINE.replace('  script:\n    - npm test','  image: node:22\n  script:\n    - npm test'),
            PIPELINE.replace('  script:\n    - npm test','  extends: .template\n  script:\n    - npm test'),
            PIPELINE.replace('stages:\n','include:\n  - local: other.yml\nstages:\n'),
        ]
        for x in examples:
            with self.subTest(text=x),self.assertRaises(RuntimeError):
                gf.migrate(x)

    def test_read_and_assess_gitlab_file(self):
        value={'encoding':'base64','content':base64.b64encode(PIPELINE.encode()).decode(),
               'last_commit_id':'a'*40}
        with patch('gitlab_fleet._json_object',return_value=value):
            plan=gf.assess('team/repo','main')
        self.assertEqual(plan.state,'review-ready')
        self.assertEqual(plan.jobs,2)
        self.assertEqual(plan.last_commit_id,'a'*40)

    def test_fail_closed_when_config_has_wrong_encoding(self):
        with patch('gitlab_fleet._json_object',return_value={'encoding':'text','content':PIPELINE,'last_commit_id':'a'*40}):
            self.assertEqual(gf.assess('team/repo','main').state,'manual')

class GitLabFleetSafetyTests(unittest.TestCase):
    def test_scoped_group_pagination_and_fork_rejection(self):
        rows=[{'path_with_namespace':'Org/app','default_branch':'main','archived':False},
              {'path_with_namespace':'Org/sub/tool','default_branch':'main','archived':False},
              {'path_with_namespace':'Other/leak','default_branch':'main','archived':False},
              {'path_with_namespace':'Org/old','default_branch':'main','archived':True}]
        with patch('gitlab_fleet.invoke_json',return_value=rows) as api:
            result=gf.discover('Org')
        self.assertEqual(result,[('Org/app','main'),('Org/sub/tool','main')])
        self.assertIn('groups/Org/projects',api.call_args.args[0][2])

    def test_group_policy_is_private_and_explicit(self):
        with tempfile.TemporaryDirectory() as td,patch.object(gf,'POLICY',Path(td)/'.local'/'groups.json'):
            with self.assertRaises(PermissionError):
                gf.authorize('Org')
            gf.authorize('Org',confirm=True)
            self.assertTrue(gf.approved('org'))
            self.assertEqual(gf.POLICY.stat().st_mode & 0o077,0)

    def test_refuses_unapproved_project_without_remote_calls(self):
        p=gf.Plan('Org/repo','main','review-ready','ok',PIPELINE,'a'*40,1)
        with patch('gitlab_fleet.policy',return_value={'groups':[]}),\
             patch('gitlab_fleet._json_object') as api:
            with self.assertRaises(PermissionError):
                gf.create_mr(p)
            api.assert_not_called()

    def test_refuses_missing_native_runner_without_writes(self):
        p=gf.Plan('Org/repo','main','review-ready','ok',PIPELINE,'a'*40,1)
        with patch('gitlab_fleet.policy',return_value={'groups':['Org']}),\
             patch('gitlab_fleet.existing_mr',return_value=False),\
             patch('gitlab_fleet.check_readiness',return_value={'verified':False}),\
             patch('gitlab_fleet._json_object') as api:
            with self.assertRaisesRegex(RuntimeError,'unavailable'):
                gf.create_mr(p)
            api.assert_not_called()

    def test_creates_review_mr_and_never_merges(self):
        p=gf.Plan('Org/repo','main','review-ready','ok',PIPELINE,'a'*40,1)
        with patch('gitlab_fleet.policy',return_value={'groups':['Org']}),\
             patch('gitlab_fleet.existing_mr',return_value=False),\
             patch('gitlab_fleet.check_readiness',return_value={'verified':True}),\
             patch('gitlab_fleet.assess',return_value=p),\
             patch('gitlab_fleet.secrets.token_hex',return_value='abc012'),\
             patch('gitlab_fleet._json_object',side_effect=[
                 {'default_branch':'main'}, {}, {}, {'web_url':'https://gitlab.com/Org/repo/-/merge_requests/3'}]) as api:
            url=gf.create_mr(p)
        self.assertEqual(url,'https://gitlab.com/Org/repo/-/merge_requests/3')
        update=' '.join(api.call_args_list[2].args[0])
        self.assertIn('last_commit_id='+'a'*40,update)
        self.assertIn('branch=open-ci-hub/fleet-abc012',update)
        self.assertFalse(any('merge_when_pipeline_succeeds' in ' '.join(z.args[0]) for z in api.call_args_list))

if __name__ == '__main__':
    unittest.main()
