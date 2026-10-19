import unittest
from unittest.mock import patch
import pipeline_details as detail
from providers import circleci_cockpit as circle
from tests import test_web_console

WID = '5034460f-c7c4-4c43-9457-de07e2029e7b'


class DetailTests(unittest.TestCase):
    def test_github_jobs_show_runner_and_conclusion(self):
        with patch.object(detail, 'invoke_json', return_value={'jobs': [{'id': 7, 'name': 'Tests', 'status': 'completed', 'conclusion': 'success', 'runner_name': 'own-pc'}]}):
            job = detail.list_jobs('github', 'a/b', '9')[0]
            self.assertEqual(job['status'], 'success')
            self.assertEqual(job['runner'], 'own-pc')

    def test_gitlab_jobs_nested_project_and_no_runner_yet(self):
        with patch.object(detail, 'invoke_json', return_value=[{'id': 2, 'runner': None, 'status': 'pending'}]) as api:
            self.assertEqual(detail.list_jobs('gitlab', 'a/sub/b', '9')[0]['runner'], 'Waiting for worker')
            self.assertIn('projects/a%2Fsub%2Fb/pipelines/9/jobs?per_page=100', api.call_args.args[0])

    def test_job_log_membership_checked_before_trace(self):
        with patch.object(detail, 'list_jobs', return_value=[]), patch.object(detail, 'invoke') as invoke:
            with self.assertRaises(ValueError):
                detail.job_log('github', 'a/b', '9', '8')
            invoke.assert_not_called()

    def test_logs_redacted_and_terminal_controls_removed(self):
        text = '\x1b[31mhello\x1b[0m ghp_testsecret glrt-testsecret token=veryprivate Authorization: Bearer sensitive https://user:pass@server.invalid/\x00'
        out = detail.clean_log(text)
        for secret in ['ghp_testsecret', 'glrt-testsecret', 'veryprivate', 'sensitive', 'user:pass', '\x1b', '\x00']:
            self.assertNotIn(secret, out)
        self.assertIn('hello', out)
        self.assertIn('[REDACTED]', out)
        self.assertEqual(len(detail.clean_log('x'*150000)), 120000)

    def test_github_artifacts_use_native_links_and_expiry(self):
        with patch.object(detail, 'invoke_json', return_value={'artifacts': [{'id': 8, 'name': 'report', 'expired': True, 'size_in_bytes': 5}]}):
            artifact = detail.list_artifacts('github', 'a/b', '9')[0]
            self.assertTrue(artifact['expired'])
            self.assertEqual(artifact['url'], 'https://github.com/a/b/actions/runs/9/artifacts/8')

    def test_gitlab_artifacts_keep_self_managed_origin(self):
        with patch.object(detail, 'invoke_json', return_value=[{'web_url': 'https://gitlab.example/a/b/-/jobs/8', 'artifacts_file': {'filename': 'archive.zip', 'size': 5}}]):
            self.assertEqual(detail.list_artifacts('gitlab', 'a/b', '9')[0]['url'], 'https://gitlab.example/a/b/-/jobs/8/artifacts/browse')

    def test_circleci_lists_native_workflows(self):
        with patch.object(circle, 'invoke_json', return_value=[{'id': WID, 'name': 'test', 'outcome': 'success'}]) as cli:
            self.assertEqual(circle.list_pipelines('a/b')[0]['status'], 'success')
            self.assertIn('gh/a/b', cli.call_args.args[0])

    def test_circleci_rejects_workflow_outside_selected_project(self):
        with patch.object(circle, 'workflows', return_value=[]), patch.object(circle, 'invoke') as mutation:
            with self.assertRaises(ValueError):
                circle.pipeline_action('a/b', WID, 'retry')
            mutation.assert_not_called()

    def test_circleci_uuid_and_confirmed_mutation(self):
        with patch.object(circle, 'workflows', return_value=[{'id': WID}]), patch.object(circle, 'invoke') as mutation:
            circle.pipeline_action('a/b', WID, 'cancel')
            self.assertEqual(mutation.call_args.args[0], ['circleci','workflow','cancel',WID,'--force'])
        for value in ['123', '--force', WID+'\n', None]:
            with self.assertRaises(ValueError): circle.workflow_id(value)


class DetailHttpTests(test_web_console.DashboardHTTPTests):
    def test_details_require_session(self):
        for route in ['jobs', 'job-log', 'artifacts']:
            status, *_ = self.request('GET', '/api/'+route+'?provider=github&repo=a/b&run=9&job=8')
            self.assertEqual(status, 401)

    def test_job_list_and_trace_error(self):
        with patch('web_console.list_jobs', return_value=[{'id': '8', 'name': 'Tests'}]):
            status, data, _ = self.request('GET', '/api/jobs?provider=github&repo=a/b&run=9', cookie=self.session())
            self.assertEqual(status, 200)
            self.assertEqual(data['items'][0]['id'], '8')
        with patch('web_console.job_log', side_effect=ValueError('Job not in pipeline')):
            status, *_ = self.request('GET', '/api/job-log?provider=github&repo=a/b&run=9&job=8', cookie=self.session())
            self.assertEqual(status, 400)
