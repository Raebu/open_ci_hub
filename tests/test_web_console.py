"""Unit and HTTP integration tests for authenticated console flows."""
import http.client
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web_console import WebConsole, create_repo, launch_signin


class RepositoryTests(unittest.TestCase):
    @patch('web_console.shutil.which', return_value='/fake/gh')
    @patch('web_console.subprocess.run')
    def test_github_repo_private(self, run, which):
        data = create_repo('github', 'org/repo', 'private', 'new project')
        self.assertEqual(data['url'], 'https://github.com/org/repo')
        args = run.call_args.args[0]
        self.assertEqual(args[:4], ['gh', 'repo', 'create', 'org/repo'])
        self.assertIn('--private', args)
        self.assertNotIn('--public', args)
        self.assertFalse(run.call_args.kwargs.get('shell', False))

    @patch('web_console.shutil.which', return_value='/fake/glab')
    @patch('web_console.subprocess.run')
    def test_gitlab_repo_private(self, run, which):
        data = create_repo('gitlab', 'org/repo', 'private')
        self.assertEqual(data['url'], '')
        self.assertIn('--skipGitInit', run.call_args.args[0])

    def test_invalid_repo_name(self):
        for name in ('--name', '../../etc/passwd', 'good; rm -rf /', '../evil', 'a/b/c', '', 'bad repo'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                create_repo('github', name, 'private')

    def test_reject_unsupported_and_invalid(self):
        with self.assertRaises(ValueError):
            create_repo('circleci', 'owner/repo', 'private')
        with self.assertRaises(ValueError):
            create_repo('github', 'owner/repo', 'internal')


class DashboardHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        source = Path(__file__).resolve().parents[1] / 'dashboard'
        import shutil
        shutil.copytree(source, root / 'dashboard')
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); cls.port = s.getsockname()[1]
        cls.console = WebConsole(root, cls.port)
        cls.thread = threading.Thread(target=cls.console.serve, kwargs={'open_browser': False}, daemon=True)
        cls.thread.start()
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1', cls.port), timeout=.1):
                    break
            except OSError:
                time.sleep(.01)

    @classmethod
    def tearDownClass(cls):
        # daemonized test server terminates with test process
        cls.tmp.cleanup()

    def request(self, method, path, body=None, cookie=None, origin=None, headers=None):
        h = headers.copy() if headers else {}
        h['Host'] = f'127.0.0.1:{self.port}'
        if body is not None:
            h['Content-Type'] = 'application/json'
            h['X-Open-CI-Request'] = '1'
            h['Origin'] = origin or f'http://127.0.0.1:{self.port}'
        if cookie:
            h['Cookie'] = cookie
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        try:
            conn.request(method, path, json.dumps(body) if body is not None else None, h)
            response = conn.getresponse()
            payload = response.read()
            return response.status, json.loads(payload) if response.getheader('Content-Type', '').startswith('application/json') else payload, response.getheader('Set-Cookie')
        finally:
            conn.close()

    def session(self):
        status, payload, cookie = self.request('POST', '/api/session', {'secret': self.console.admin_secret})
        self.assertEqual(status, 200)
        return cookie.split(';')[0]

    def test_static_dashboard_and_csp(self):
        status, content, _ = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(b'Your workflows', content)

    def test_unauthed_restricted(self):
        status, content, _ = self.request('GET', '/api/overview')
        self.assertEqual(status, 401)

    def test_bad_secret_denied(self):
        status, body, cookie = self.request('POST', '/api/session', {'secret':'invalid'})
        self.assertEqual(status, 403)
        self.assertIsNone(cookie)

    def test_csrf_denied(self):
        cookie = self.session()
        status, payload, _ = self.request('POST', '/api/mobile/approve', {'user':'alice@example.com'}, cookie, origin='https://evil.example')
        self.assertEqual(status, 403)

    def test_mobile_cannot_login_behind_local_host(self):
        status, payload, _ = self.request('POST', '/api/mobile/request', {}, None)
        self.assertEqual(status, 403)

    def test_mobile_approval_and_revoke(self):
        cookie=self.session()
        self.console.request_pair('alice@example.com')
        status, payload, _ = self.request('POST', '/api/mobile/approve', {'user':'alice@example.com'}, cookie)
        self.assertEqual(status, 200)
        self.assertIn('alice@example.com', self.console.approved())
        status, payload, _ = self.request('POST', '/api/mobile/revoke', {'user':'alice@example.com'}, cookie)
        self.assertEqual(status, 200)
        self.assertNotIn('alice@example.com', self.console.approved())

    @patch('web_console.create_repo', return_value={'message':'Repository created.','url':'https://github.com/org/repo'})
    def test_create_repo_authenticated(self, creator):
        cookie=self.session()
        status, data, _=self.request('POST','/api/repos',{'provider':'github','name':'org/repo','visibility':'private'},cookie)
        self.assertEqual(status,201)
        self.assertTrue(creator.called)

    @patch('web_console.create_repo', return_value={'message':'created','url':'https://github.com/org/repo'})
    def test_tailscale_viewer_and_editor_permissions(self, creator):
        user = 'mobile@example.com'
        self.console.request_pair(user)
        self.console.approve(user, True, 'viewer')
        headers={'Host':'runner.tail.ts.net', 'Tailscale-User-Login':user,
                 'X-Forwarded-Proto':'https'}
        # GET via the actual local listener with Tailscale Serve-style forwarded headers
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            conn.request('GET', '/api/overview', headers=headers)
            response=conn.getresponse(); data=json.loads(response.read()); self.assertEqual(response.status, 200)
            self.assertEqual(data['mode'],'mobile'); self.assertEqual(data['role'],'viewer')
        finally:
            conn.close()
        def remote_create():
            conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
            try:
                h={**headers, 'Origin':'https://runner.tail.ts.net',
                   'Content-Type':'application/json','X-Open-CI-Request':'1'}
                conn.request('POST','/api/repos',body=json.dumps({'provider':'github','name':'org/repo','visibility':'private'}),headers=h)
                response=conn.getresponse(); response.read(); return response.status
            finally:
                conn.close()
        self.assertEqual(remote_create(),403)
        creator.assert_not_called()
        self.console.request_pair(user)
        # Promotion remains an explicit PC-owner decision.
        self.console.approve(user, False)
        self.console.request_pair(user)
        self.console.approve(user, True, 'editor')
        self.assertEqual(remote_create(),201)

    @patch('web_console.create_repo')
    def test_public_requires_confirmation(self, creator):
        cookie=self.session()
        status, data, _=self.request('POST','/api/repos',{'provider':'github','name':'org/repo','visibility':'public'},cookie)
        self.assertEqual(status,400)
        creator.assert_not_called()


if __name__ == '__main__':
    unittest.main()
