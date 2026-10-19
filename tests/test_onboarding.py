"""Native-workflow onboarding must not silently replace existing provider CI."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import onboarding

class PlanTests(unittest.TestCase):
    def test_push_triggered_github_workflow_on_self_hosted_only(self):
        p=onboarding.make_plan('github','example/service')
        self.assertEqual(p.path,'.github/workflows/open-ci-hub.yml')
        self.assertIn('push:',p.content)
        self.assertIn('runs-on: [self-hosted, linux, local-ci]',p.content)
        self.assertNotIn('ubuntu-latest',p.content)
        self.assertNotIn('pull_request:',p.content)

    def test_gitlab_protected_tagged_workflow(self):
        p=onboarding.make_plan('gitlab','team/subgroup/repo',branch='main')
        self.assertIn('tags: [local-ci]',p.content)
        self.assertIn('CI_COMMIT_BRANCH',p.content)
        self.assertNotIn('ubuntu-latest',p.content)

    def test_circleci_resource_class_with_branch_filter(self):
        p=onboarding.make_plan('circleci','owner/repo',resource_class='namespace/workers')
        self.assertIn('resource_class: namespace/workers',p.content)
        self.assertIn('only: main',p.content)
        self.assertIn('machine: true',p.content)

    def test_reject_invalid_repo_branch_or_resource_class(self):
        for provider,repo,branch,resource in [('github','bad;rm -rf /','main',''),
                                              ('gitlab','owner/repo','../../p',''),
                                              ('circleci','owner/repo','main','bad token'),
                                              ('circleci','owner/repo','main','a/b\nrun: curl')]:
            with self.subTest(provider=provider,repo=repo),self.assertRaises(ValueError):
                onboarding.make_plan(provider,repo,branch=branch,resource_class=resource)

    def test_approval_required(self):
        with patch.object(onboarding,'_create_github_pr') as create:
            with self.assertRaises(PermissionError):
                onboarding.create_review_request(onboarding.make_plan('github','owner/repo'))
            create.assert_not_called()

    def test_existing_github_workflow_not_overwritten(self):
        with patch.object(onboarding,'_get_github_branch_and_tree',return_value=('main',{'.github/workflows/open-ci-hub.yml'})), \
             patch.object(onboarding,'_json_object') as send:
            with self.assertRaisesRegex(RuntimeError,'already exists'):
                onboarding.create_review_request(onboarding.make_plan('github','owner/repo'),approved=True)
            send.assert_not_called()

    def test_github_request_contains_branch_and_workflow_and_pr(self):
        response=[{'object':{'sha':'b'*40}}, {}, {}, {'html_url':'https://github.com/owner/repo/pull/5'}]
        with patch.object(onboarding,'_get_github_branch_and_tree',return_value=('main',set())), \
             patch.object(onboarding,'_json_object',side_effect=response) as invoked, \
             patch.object(onboarding.secrets,'token_hex',return_value='deadbeef00'):
            out=onboarding.create_review_request(onboarding.make_plan('github','owner/repo'),approved=True)
        self.assertEqual(out['url'],'https://github.com/owner/repo/pull/5')
        self.assertIn('ref=refs/heads/open-ci-hub/onboard-deadbeef00',invoked.call_args_list[1].args[0])
        self.assertTrue(any('/contents/.github/workflows/open-ci-hub.yml' in ' '.join(c.args[0]) for c in invoked.call_args_list))

    def test_default_branch_mismatch_fails_before_write(self):
        with patch.object(onboarding,'_get_github_branch_and_tree',return_value=('master',set())), \
             patch.object(onboarding,'_json_object') as sent:
            with self.assertRaisesRegex(ValueError,'does not match'):
                onboarding.create_review_request(onboarding.make_plan('github','owner/repo'),approved=True)
            sent.assert_not_called()

    def test_gitlab_create_only_api(self):
        responses=[{'default_branch':'main'}, {}, {}, {'web_url':'https://gitlab.com/team/proj/-/merge_requests/1'}]
        with patch.object(onboarding,'_json_object',side_effect=responses) as called, \
             patch.object(onboarding.secrets,'token_hex',return_value='aaabbbccc0'):
            outcome=onboarding.create_review_request(onboarding.make_plan('gitlab','team/proj'),approved=True)
        self.assertTrue(any('merge_requests' in arg for arg in called.call_args_list[-1].args[0]))
        self.assertTrue(any('/repository/files/' in str(c.args[0]) for c in called.call_args_list))
        self.assertEqual(outcome['branch'],'open-ci-hub/onboard-aaabbbccc0')

    def test_repo_error_fails_closed(self):
        with patch.object(onboarding,'_json_object',side_effect=RuntimeError('Cannot verify repository')):
            with self.assertRaises(RuntimeError):
                onboarding.create_review_request(onboarding.make_plan('github','owner/repo'),approved=True)

    def test_group_runner_creation(self):
        from providers import gitlab
        with patch.object(gitlab, 'response_object', side_effect=[{'id': 123}, {'id': 25, 'token': 'glrt-abc'}]) as calls, \
             patch.object(gitlab, 'authenticated', return_value=True), \
             patch('builtins.input', side_effect=['https://gitlab.com/team/sub', 'garuda-group']):
            result = gitlab.prepare_group()
        self.assertEqual(result.metadata['scope'], 'group')
        self.assertEqual(result.metadata['group'], 'https://gitlab.com/team/sub')
        self.assertIn('runner_type=group_type', calls.call_args.args[0])
        self.assertIn('group_id=123', calls.call_args.args[0])
        self.assertIn('run_untagged=false', calls.call_args.args[0])

    def test_api_guards_present(self):
        root=Path(__file__).resolve().parents[1]
        console=(root/'web_console.py').read_text()
        self.assertIn("if console.role(self) not in ('owner', 'editor'):",console)
        self.assertIn('Explicit approval required to modify a provider repository',console)
        self.assertIn('onboarding.review',console)
        self.assertIn('api/onboarding/plan',console)

if __name__=='__main__':unittest.main()

class OnboardingHttpTests(__import__('tests.test_web_console',fromlist=['DashboardHTTPTests']).DashboardHTTPTests):
    def test_preview_needs_owner(self):
        status, *_=self.request('POST','/api/onboarding/plan',{'provider':'github','repo':'owner/repo'})
        self.assertEqual(status,401)
        status, response, _=self.request('POST','/api/onboarding/plan',{'provider':'github','repo':'owner/repo'},cookie=self.session())
        self.assertEqual(status,200)
        self.assertIn('runs-on: [self-hosted, linux, local-ci]',response['content'])

    def test_apply_requires_explicit_approval(self):
        from unittest.mock import patch
        with patch('onboarding.create_review_request', return_value={'url':'https://github.com/owner/repo/pull/10','message':'Created'}) as creator:
            status, *_=self.request('POST','/api/onboarding/apply',{'provider':'github','repo':'owner/repo'},cookie=self.session())
            self.assertEqual(status,400)
            creator.assert_not_called()
            status,response,_=self.request('POST','/api/onboarding/apply',{'provider':'github','repo':'owner/repo','confirm':True},cookie=self.session())
            self.assertEqual(status,201)
            creator.assert_called_once()

    def test_apply_blocks_invalid_repos(self):
        from unittest.mock import patch
        with patch('onboarding.create_review_request', return_value={'url':'https://github.com/owner/repo/pull/10','message':'Created'}) as creator:
            status,*_=self.request('POST','/api/onboarding/apply',{'provider':'gitlab','repo':'team/repo;evil','confirm':True},cookie=self.session())
            self.assertEqual(status,400)
            creator.assert_not_called()
