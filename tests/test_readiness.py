import unittest
from unittest.mock import patch
import onboarding
import readiness


class ReadinessTests(unittest.TestCase):
    def github(self, runners):
        return [{'permissions': {'push': True}}, {'enabled': True}, {'runners': runners}]

    def runner(self, **changes):
        return dict({'status': 'online', 'name': 'trusted-pc', 'busy': False,
                     'labels': [{'name': n} for n in ['self-hosted', 'Linux', 'local-ci']]}, **changes)

    def test_github_online_busy_worker_can_queue(self):
        with patch('readiness.invoke_json', side_effect=self.github([self.runner(busy=True)])):
            result = readiness.check('github', 'a/b', 'main')
        self.assertTrue(result['verified'])
        self.assertTrue(result['runners'][0]['busy'])

    def test_offline_wrong_labels_and_malformed_fail_closed(self):
        for runner in [self.runner(status='offline'), self.runner(labels=[]), self.runner(labels=None), None]:
            with self.subTest(runner=runner), patch('readiness.invoke_json', side_effect=self.github([runner])):
                with self.assertRaisesRegex(RuntimeError, 'No online eligible'):
                    readiness.check('github', 'a/b', 'main')

    def test_permission_and_actions_policy(self):
        for response in [[{'permissions': {'push': False}}], [{'permissions': {'push': True}}, {'enabled': False}]]:
            with patch('readiness.invoke_json', side_effect=response), self.assertRaises(RuntimeError):
                readiness.check('github', 'a/b', 'main')

    def test_gitlab_excludes_shared_and_paused_compute(self):
        for runner in [{'runner_type': 'instance_type', 'status': 'online', 'paused': False, 'id': 1},
                       {'runner_type': 'project_type', 'status': 'online', 'paused': True, 'id': 2}]:
            with patch('readiness.invoke_json', side_effect=[{'protected': True}, [runner], []]) as api:
                with self.assertRaisesRegex(RuntimeError, 'No online protected'):
                    readiness.check('gitlab', 'a/sub/b', 'main')
                self.assertEqual(api.call_count, 3)

    def test_gitlab_checks_protection_tags_and_scope(self):
        listing = [{'id': 7, 'runner_type': 'group_type', 'paused': False, 'status': 'online'}]
        detail = {'tag_list': ['local-ci'], 'access_level': 'ref_protected', 'paused': False, 'status': 'online'}
        with patch('readiness.invoke_json', side_effect=[{'protected': True}, listing, [], detail]) as api:
            self.assertTrue(readiness.check('gitlab', 'a/sub/b', 'main')['verified'])
            self.assertIn('projects/a%2Fsub%2Fb/runners?type=project_type&per_page=100', api.call_args_list[1].args[0])
        for change in [{'access_level': 'not_protected'}, {'tag_list': []}, {'paused': True}]:
            with patch('readiness.invoke_json', side_effect=[{'protected': True}, listing, [], dict(detail, **change)]):
                with self.assertRaises(RuntimeError):
                    readiness.check('gitlab', 'a/b', 'main')

    def test_gitlab_unprotected_branch_rejected(self):
        with patch('readiness.invoke_json', return_value={'protected': False}) as api:
            with self.assertRaisesRegex(RuntimeError, 'Protect'):
                readiness.check('gitlab', 'a/b', 'main')
            api.assert_called_once()

    def test_unverified_provider_cannot_create_review_request(self):
        for provider, resource in [('circleci', 'org/worker'), ('github', '')]:
            plan = onboarding.make_plan(provider, 'owner/repo', resource_class=resource)
            with self.subTest(provider=provider), \
                 patch('onboarding.check_readiness', return_value={'verified': False}), \
                 patch('onboarding._create_github_pr') as write:
                with self.assertRaisesRegex(RuntimeError, 'readiness is unverified'):
                    onboarding.create_review_request(plan, approved=True)
                write.assert_not_called()

    def test_circleci_remains_unverified(self):
        with patch('readiness.invoke_json') as api:
            self.assertFalse(readiness.check('circleci', 'a/b', 'main', 'org/worker')['verified'])
            api.assert_not_called()

    def test_preflight_failure_prevents_all_writes(self):
        with patch('onboarding.check_readiness', side_effect=RuntimeError('Offline')), patch('onboarding._create_github_pr') as write:
            with self.assertRaisesRegex(RuntimeError, 'Offline'):
                onboarding.create_review_request(onboarding.make_plan('github', 'a/b'), approved=True)
            write.assert_not_called()

    def test_invalid_github_namespace_and_yaml_ambiguous_branch(self):
        for provider in ['github', 'circleci']:
            with self.assertRaises(ValueError):
                onboarding.make_plan(provider, 'a/sub/b', resource_class='org/worker')
        self.assertIn('branches: ["123"]', onboarding.make_plan('github', 'a/b', branch='123').content)
        self.assertIn('only: "true"', onboarding.make_plan('circleci', 'a/b', branch='true', resource_class='org/worker').content)


from tests import test_web_console


class ReadinessHttpTests(test_web_console.DashboardHTTPTests):
    def test_readiness_requires_session_and_returns_worker_evidence(self):
        body = {'provider': 'github', 'repo': 'a/b'}
        status, *_ = self.request('POST', '/api/onboarding/check', body)
        self.assertEqual(status, 401)
        with patch('readiness.check', return_value={'verified': True, 'runners': [{'name': 'pc'}]}) as check:
            status, data, _ = self.request('POST', '/api/onboarding/check', body, cookie=self.session())
            self.assertEqual(status, 200)
            self.assertTrue(data['verified'])
            check.assert_called_once_with('github', 'a/b', 'main', '')

    def test_readiness_provider_failure_does_not_report_ready(self):
        with patch('readiness.check', side_effect=RuntimeError('No online eligible runner')):
            status, data, _ = self.request('POST', '/api/onboarding/check', {'provider': 'github', 'repo': 'a/b'}, cookie=self.session())
            self.assertEqual(status, 400)
            self.assertIn('No online', data['error'])
