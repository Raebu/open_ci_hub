"""Unit and authenticated HTTP tests for v4 provider-neutral management."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import operations
from audit import AuditLog
from tests import test_web_console

class OperationTests(unittest.TestCase):
    def test_repo_validation_blocks_command_injection(self):
        for path in ('--all/repo', '../secret', '../../etc/shadow', 'a/b;foo', 'a/b/c/..',
                     'a/b\\c', 'a/b\nX=1', 'only-one-part', '/a/b', 'a/../b', 'a/'+'x'*400):
            with self.subTest(path=path), self.assertRaises(ValueError):
                operations.safe_repo(path)
        self.assertEqual(operations.safe_repo('group/subgroup/project'), 'group/subgroup/project')

    def test_id_validation(self):
        for n in ('', '0','-1','1;rm', '2.1', 'a', str(10**18)):
            with self.subTest(n=n), self.assertRaises(ValueError):
                operations.safe_run_id(n)
        self.assertEqual(operations.safe_run_id(42), '42')

    @patch('operations.invoke_json',return_value=[{'nameWithOwner':'org/repo','isPrivate':True,'url':'https://github.com/org/repo'}])
    def test_github_repository_inventory(self, invoke):
        self.assertEqual(operations.list_repositories('github')[0]['name'],'org/repo')
        self.assertEqual(invoke.call_args.args[0][:3],['gh','repo','list'])
        self.assertIn('--json', invoke.call_args.args[0])

    @patch('operations.invoke_json',return_value=[{'path_with_namespace':'g/p','visibility':'private','web_url':'https://gitlab.com/g/p'}])
    def test_gitlab_repository_inventory(self, invoke):
        self.assertEqual(operations.list_repositories('gitlab')[0]['name'],'g/p')
        self.assertIn('--member', invoke.call_args.args[0])
        with self.assertRaises(ValueError): operations.list_repositories('gitlab','group')

    @patch('operations.invoke_json',return_value=[{'databaseId':123,'displayTitle':'Tests','status':'completed','conclusion':'failure'}])
    def test_github_pipelines(self, invoke):
        self.assertEqual(operations.list_pipelines('github','owner/repo')[0]['id'],'123')
        self.assertIn('--repo', invoke.call_args.args[0])

    @patch('operations.invoke_json',return_value=[{'id':123,'status':'success','ref':'main'}])
    def test_gitlab_pipelines(self, invoke):
        self.assertEqual(operations.list_pipelines('gitlab','owner/repo')[0]['status'],'success')
        self.assertIn('--repo', invoke.call_args.args[0])

    @patch('operations.invoke')
    def test_run_action_cli_is_safe_and_bounded(self, invoke):
        with self.assertRaises(PermissionError):
            operations.pipeline_action('github','org/repo',123,'retry')
        invoke.assert_not_called()
        operations.pipeline_action('gitlab','org/repo','12','cancel')
        self.assertEqual(invoke.call_args.args[0],['glab','ci','cancel','pipeline','12','--repo','org/repo'])
        with self.assertRaises(PermissionError):
            operations.pipeline_action('gitlab','a/b',12,'retry')
        with self.assertRaises(ValueError): operations.pipeline_action('github','owner/repo',123,'delete')
        with self.assertRaises(PermissionError): operations.pipeline_action('gitlab','owner/repo','invalid','retry')
        with self.assertRaises(ValueError): operations.pipeline_action('circleci','owner/repo',123,'cancel')

    @patch('operations.shutil.which', return_value='/usr/bin/gh')
    @patch('operations.subprocess.run', return_value=MagicMock(returncode=1,stderr='TOKEN_SECRET',stdout=''))
    def test_provider_errors_do_not_leak_cli_output(self, run, which):
        with self.assertRaises(RuntimeError) as context:
            operations.invoke(['gh','run','list'])
        self.assertNotIn('TOKEN_SECRET', str(context.exception))
        self.assertIs(run.call_args.kwargs.get('shell'), None)
        self.assertIs(run.call_args.kwargs['stdin'], __import__('subprocess').DEVNULL)

    def test_audit_persists_no_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'.local'/'audit.db'
            audit=AuditLog(path)
            audit.add('local-owner','repo.create','github:owner/repo','success')
            self.assertEqual(AuditLog(path).recent()[0]['target'],'github:owner/repo')
            if os.name != 'nt':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
                evil=Path(folder)/'outside.db'
                path.unlink();path.symlink_to(evil)
                with self.assertRaises(RuntimeError): AuditLog(path)

class NewHttpTests(test_web_console.DashboardHTTPTests):
    # Reuse HTTP infrastructure; inherited tests are intentionally rerun to
    # ensure v4 routes do not regress the existing v3 CSRF checks.
    @patch('web_console.list_repositories', return_value=[{'name':'acme/app'}])
    def test_repositories_endpoint_authz(self, listing):
        status, *_=self.request('GET','/api/repos?provider=github')
        self.assertEqual(status,401)
        status, payload, _=self.request('GET','/api/repos?provider=github',cookie=self.session())
        self.assertEqual(status,200)
        self.assertEqual(payload['items'][0]['name'],'acme/app')
        self.assertTrue(listing.called)

    @patch('web_console.list_pipelines',return_value=[{'id':'999','status':'failure'}])
    def test_pipeline_listing(self, listing):
        status, data, _=self.request('GET','/api/pipelines?provider=gitlab&repo=g/p',cookie=self.session())
        self.assertEqual(status,200)
        self.assertEqual(data['items'][0]['id'],'999')

    @patch('web_console.pipeline_action')
    def test_pipeline_requires_confirmation(self, action):
        cookie=self.session()
        payload={'provider':'github','repo':'acme/app','id':'123','action':'cancel'}
        status, *_=self.request('POST','/api/pipeline',payload,cookie=cookie)
        self.assertEqual(status,400)
        action.assert_not_called()
        payload['confirm']=True
        status, *_=self.request('POST','/api/pipeline',payload,cookie=cookie)
        self.assertEqual(status,200)
        action.assert_called_once()

    @patch('web_console.pipeline_action')
    def test_reject_pipeline_injection(self, action):
        cookie=self.session()
        status, *_=self.request('POST','/api/pipeline',{'provider':'github','repo':'../pass','id':'123', 'action':'cancel','confirm':True},cookie=cookie)
        self.assertEqual(status,400)
        action.assert_not_called()

    @patch('web_console.pipeline_action')
    def test_viewer_cannot_change_pipeline(self, action):
        user='mobileviewer@example.com'
        self.console.request_pair(user);self.console.approve(user,True,'viewer')
        import http.client
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        try:
            c.request('POST','/api/pipeline',json.dumps({'provider':'github','repo':'a/b','id':'1','action':'cancel','confirm':True}),
                      {'Host':'runner.tail.ts.net','Tailscale-User-Login':user,'X-Forwarded-Proto':'https',
                       'Origin':'https://runner.tail.ts.net','Content-Type':'application/json','X-Open-CI-Request':'1'})
            result=c.getresponse();result.read()
            self.assertEqual(result.status,403)
            action.assert_not_called()
        finally:c.close()

    @patch('web_console.launch_signin',return_value='GitHub setup opened.')
    def test_runner_setup_owner_only(self, launcher):
        status, result, _=self.request('POST','/api/runner/setup',{'provider':'github'}, cookie=self.session())
        self.assertEqual(status,202)
        self.assertIn('setup', result['message'])
        self.assertTrue(launcher.call_args.kwargs['setup'])
        status, *_=self.request('POST','/api/runner/setup',{'provider':'github'})
        self.assertEqual(status,401)

    def test_sign_out_revokes_desktop_session(self):
        cookie=self.session()
        status, result, _=self.request('POST','/api/logout',{}, cookie=cookie)
        self.assertEqual(status,200)
        status, _, _=self.request('GET','/api/activity',cookie=cookie)
        self.assertEqual(status,401)

    def test_audit_only_visible_to_local_owner(self):
        status,*_=self.request('GET','/api/activity')
        self.assertEqual(status,401)
        status, result, _=self.request('GET','/api/activity',cookie=self.session())
        self.assertEqual(status,200)
        self.assertIsInstance(result['items'],list)

if __name__=='__main__':unittest.main()
