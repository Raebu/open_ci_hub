"""Failure diagnostics retain actionable reasons and deny unsafe workflows."""
import unittest
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import ci_workflow_diagnostics as workflows
import ci_failure_diagnostics as failures
import pipeline_details

GOOD = """name: Native CI
on:
  push:
    branches: [main]
jobs:
  test:
    runs-on: [self-hosted, linux, local-ci]
    steps:
      - run: python3 -m unittest
"""
TREE = {'truncated': False, 'tree': [
    {'type': 'blob', 'path': '.github/workflows/build.yml'},
]}


class WorkflowDiagnosticTests(unittest.TestCase):
    def report(self, source=GOOD, tree=TREE):
        with patch('ci_workflow_diagnostics.fleet._json_object', return_value=tree), \
             patch('ci_workflow_diagnostics.fleet._file', return_value=(source, 'a'*40)):
            return workflows.inspect_github('Raebu/service', 'main')

    def test_valid_local_only_main_workflow_is_eligible(self):
        report=self.report()
        self.assertTrue(report['verified'])
        self.assertEqual(report['code'], 'eligible')
        self.assertEqual(report['issues'], [])

    def test_chatgpt_private_control_repo_is_not_a_ci_job(self):
        with patch('ci_workflow_diagnostics.fleet._json_object') as github:
            report=workflows.inspect_github('Raebu/open-ci-chatgpt-control','main')
        github.assert_not_called()
        self.assertFalse(report['verified'])
        self.assertEqual(report['code'], 'control_channel_not_ci')
        self.assertIn('Do not create GitHub Actions', report['suggestion'])

    def test_missing_workflow_gets_exact_next_action(self):
        report=self.report(tree={'truncated': False, 'tree': []})
        self.assertEqual(report['code'], 'workflow_missing')
        self.assertIn('onboarding', report['suggestion'].lower())

    def test_truncated_inventory_is_not_falsely_called_missing(self):
        report=self.report(tree={'truncated': True, 'tree': []})
        self.assertEqual(report['code'], 'workflow_tree_unverified')
        self.assertFalse(report['verified'])

    def test_pull_request_trigger_is_explained_not_silently_accepted(self):
        source=GOOD.replace('  push:', '  pull_request:\n    branches: [main]\n  push:')
        report=self.report(source=source)
        self.assertEqual(report['code'], 'trigger_not_allowlisted')
        self.assertIn('pull-request', report['message'])
        self.assertFalse(report['verified'])

    def test_hosted_or_wrong_runner_labels_are_explained(self):
        report=self.report(source=GOOD.replace(
            '[self-hosted, linux, local-ci]', 'ubuntu-latest'))
        self.assertEqual(report['code'], 'runner_label_mismatch')
        self.assertIn('self-hosted', report['suggestion'])

    def test_unreadable_workflow_does_not_authorise_runner(self):
        with patch('ci_workflow_diagnostics.fleet._json_object', return_value=TREE), \
             patch('ci_workflow_diagnostics.fleet._file', side_effect=RuntimeError('provider error')):
            result=workflows.inspect_github('Raebu/service', 'main')
        self.assertEqual(result['code'], 'workflow_unreadable')
        self.assertFalse(result['verified'])

    def test_mixed_workflows_fail_closed_with_specific_path(self):
        second={'truncated': False, 'tree': [
            {'type':'blob','path':'.github/workflows/a.yml'},
            {'type':'blob','path':'.github/workflows/b.yml'},
        ]}
        with patch('ci_workflow_diagnostics.fleet._json_object', return_value=second), \
             patch('ci_workflow_diagnostics.fleet._file', side_effect=[
                 (GOOD, 'a'*40),
                 (GOOD.replace('  push:', '  pull_request:'), 'b'*40)
             ]):
            result=workflows.inspect_github('Raebu/service', 'main')
        self.assertFalse(result['verified'])
        self.assertEqual(result['issues'][0]['workflow'], '.github/workflows/b.yml')

    def test_pool_stays_strict_and_returns_reason(self):
        import runner_pool
        with patch('ci_workflow_diagnostics.fleet._json_object', return_value=TREE), \
             patch('ci_workflow_diagnostics.fleet._file', return_value=(
                 GOOD.replace('  push:', '  pull_request:'), 'a'*40)):
            self.assertFalse(runner_pool._trusted_workflow('Raebu/service', 'main'))


class FailedJobTests(unittest.TestCase):
    def test_failed_step_reports_real_concrete_reason(self):
        payload={'jobs':[{'id':123, 'name':'unit tests', 'conclusion':'failure',
                        'runner_name':'open-ci-hub-garuda',
                        'html_url':'https://github.com/Raebu/repo/actions/runs/10/job/123',
                        'steps':[{'name':'Set up','conclusion':'success'},
                                 {'name':'Run Python tests','conclusion':'failure'}]}]}
        with patch('pipeline_details.invoke_json', return_value=payload):
            result=pipeline_details.list_jobs('github','Raebu/repo','10')
        with patch('ci_failure_diagnostics.list_jobs', return_value=result):
            report=failures.diagnose('github','Raebu/repo','10')
        self.assertEqual(report['code'], 'native_job_failed')
        self.assertIn('Run Python tests',report['failures'][0]['reason'])
        self.assertEqual(report['failures'][0]['runner'],'open-ci-hub-garuda')

    def test_gitlab_failure_reason_preserved(self):
        payload=[{'id':55,'name':'test','status':'failed', 'failure_reason':'stuck_or_timeout_failure',
                  'runner':None,'web_url':'https://gitlab.example/jobs/55'}]
        with patch('pipeline_details.invoke_json', return_value=payload):
            rows=pipeline_details.list_jobs('gitlab','Raebu/repo','10')
        with patch('ci_failure_diagnostics.list_jobs',return_value=rows):
            result=failures.diagnose('gitlab','Raebu/repo','10')
        self.assertEqual(result['failures'][0]['reason'],'stuck_or_timeout_failure')

    def test_secrets_not_echoed_by_status_endpoint(self):
        rows=[{'id':'123','name':'token=VERYPRIVATE','status':'failure',
               'failure_reason':'Authorization: Bearer abc123',
               'runner':'','failed_steps':['ghp_SECRETSECRET']}]
        with patch('ci_failure_diagnostics.list_jobs',return_value=rows):
            res=failures.diagnose('github','Raebu/repo','10')
        text=str(res)
        self.assertNotIn('VERYPRIVATE',text)
        self.assertNotIn('ghp_SECRETSECRET',text)
        self.assertNotIn('abc123',text)

    def test_real_log_lines_and_exit_code_are_preserved_with_provenance(self):
        sha = 'd' * 40
        text = ('2026-10-20 test failed\n'
                'Traceback (most recent call last):\n'
                '  File "app.py", line 23, in run\n'
                'AssertionError: expected 2 got 3\n'
                'Error: Process completed with exit code 1.\n')
        row = {'id':'9', 'name':'tests', 'status':'failure',
               'failed_steps':['Run tests'], 'runner':'local-runner', 'url':'https://github.com/a/b/actions/runs/5/job/9'}
        with patch('ci_failure_diagnostics.list_jobs', return_value=[row]), \
             patch('ci_failure_diagnostics.job_log', return_value={'text':text}), \
             patch('ci_failure_diagnostics.invoke_json', return_value={
                 'head_sha': sha, 'status':'completed','conclusion':'failure','run_attempt':2
             }) as inv:
            result=failures.diagnose('github','a/b','5',include_logs=True)
        evidence=result['failures'][0]
        self.assertEqual(evidence['log_state'],'available')
        self.assertIn('AssertionError: expected 2 got 3', evidence['log_excerpt'])
        self.assertIn('File "app.py", line 23', evidence['log_excerpt'])
        self.assertEqual(evidence['exit_code_from_log'],1)
        self.assertEqual(result['commit'],sha)
        self.assertEqual(result['attempt'],2)
        self.assertEqual(result['run_url'],'https://github.com/a/b/actions/runs/5')
        inv.assert_called_once()

    def test_no_job_logs_never_claims_an_exact_root_cause(self):
        row={'id':'9','name':'validate','status':'failure',
             'failed_steps':[], 'runner':'Waiting for worker', 'url':''}
        with patch('ci_failure_diagnostics.list_jobs',return_value=[row]), \
             patch('ci_failure_diagnostics.job_log', side_effect=RuntimeError('BlobNotFound')), \
             patch('ci_failure_diagnostics.invoke_json',side_effect=RuntimeError('missing metadata')):
            result=failures.diagnose('github','a/b','5',include_logs=True)
        job=result['failures'][0]
        self.assertEqual(job['log_state'],'unavailable')
        self.assertEqual(job['log_excerpt'],'')
        self.assertFalse(job['runner_assigned'])
        self.assertIsNone(job['exit_code_from_log'])
        self.assertIn('not expose',job['reason'])
        self.assertEqual(result['pipeline_status'],'unverified')

    def test_provider_secret_redaction_before_chatgpt_excerpt(self):
        row={'id':'3','name':'build','status':'failure',
             'failed_steps':['Run'], 'runner':'Garuda','url':''}
        leaked='fatal: token=NEVERDISPLAY\nError: Authorization: Bearer supersecret\nAssertionError: failed\n'
        with patch('ci_failure_diagnostics.list_jobs',return_value=[row]), \
             patch('ci_failure_diagnostics.job_log',return_value={'text':leaked}), \
             patch('ci_failure_diagnostics.invoke_json',return_value={}):
            result=failures.diagnose('github','a/b','5',include_logs=True)
        output=result['failures'][0]['log_excerpt']
        self.assertNotIn('NEVERDISPLAY',output)
        self.assertNotIn('supersecret',output)
        self.assertIn('AssertionError: failed',output)
        self.assertIn('[REDACTED]',output)

    def test_error_excerpts_are_bounded_and_reveal_kind(self):
        text=('unimportant step\n' * 1500 + 'ERROR: smoke failed\n' + 'exit code 2\n')
        evidence=failures._evidence_excerpt(text)
        self.assertLessEqual(len(evidence['log_excerpt']), failures.MAX_EXCERPT_CHARS)
        self.assertEqual(evidence['excerpt_kind'],'error_context')
        self.assertTrue(evidence['excerpt_truncated'])

    def test_log_fetch_is_opt_in_and_bounded(self):
        rows=[{'id':str(i),'name':'test','status':'failure','failed_steps':[],
               'runner':'host','url':''} for i in range(5)]
        with patch('ci_failure_diagnostics.list_jobs',return_value=rows), \
             patch('ci_failure_diagnostics.job_log',return_value={'text':'ERROR: failure'}) as fetch, \
             patch('ci_failure_diagnostics.invoke_json',return_value={}):
            report=failures.diagnose('github','a/b','5',include_logs=True)
        self.assertEqual(fetch.call_count,failures.MAX_LOG_JOBS)
        self.assertEqual(report['failures'][-1]['log_state'],'not_requested')

    def test_pending_jobs_not_falsely_marked_failed(self):
        with patch('ci_failure_diagnostics.list_jobs',return_value=[
                {'status':'queued','id':'12','name':'CI waiting'}]):
            result=failures.diagnose('github','Raebu/repo','10')
        self.assertEqual(result['code'],'jobs_not_finished')
        self.assertFalse(result['failures'])


class DashboardSyntaxTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node not installed on this test worker')
    def test_pipeline_error_renderer_javascript_parses(self):
        script=Path(__file__).resolve().parents[1] / 'dashboard' / 'app.js'
        result=subprocess.run([shutil.which('node'), '--check', str(script)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


from tests.test_web_console import DashboardHTTPTests


class CIDiagnosticHTTPTests(DashboardHTTPTests):
    def test_ci_routes_require_login(self):
        for url in ('/api/ci/workflow?provider=github&repo=Raebu/repo',
                    '/api/ci/failure?provider=github&repo=Raebu/repo&run=12'):
            response, *_=self.request('GET',url)
            self.assertEqual(response,401)

    def test_log_request_is_explicit_and_validated(self):
        with patch('ci_failure_diagnostics.diagnose',return_value={'code':'native_job_failed'}) as spy:
            status,data,_=self.request('GET',
                '/api/ci/failure?provider=github&repo=Raebu/repo&run=12&logs=1',
                cookie=self.session())
        self.assertEqual(status,200)
        self.assertTrue(spy.call_args.kwargs['include_logs'])
        status,data,_=self.request('GET',
            '/api/ci/failure?provider=github&repo=Raebu/repo&run=12&logs=maybe',
            cookie=self.session())
        self.assertEqual(status,400)

    def test_authenticated_diagnostics_return_precise_payload(self):
        expected={'verified':False,'code':'trigger_not_allowlisted','message':'PR event blocked'}
        with patch('fleet._json_object',return_value={'default_branch':'main'}), \
             patch('ci_workflow_diagnostics.inspect_github',return_value=expected):
            status,data,_=self.request('GET',
                '/api/ci/workflow?provider=github&repo=Raebu/repo',cookie=self.session())
        self.assertEqual(status,200)
        self.assertEqual(data['code'], 'trigger_not_allowlisted')
        with patch('ci_failure_diagnostics.diagnose',return_value={'code':'native_job_failed'}):
            status,data,_=self.request('GET',
                '/api/ci/failure?provider=github&repo=Raebu/repo&run=12',cookie=self.session())
        self.assertEqual(status,200)
        self.assertEqual(data['code'],'native_job_failed')


if __name__ == '__main__':
    unittest.main()
