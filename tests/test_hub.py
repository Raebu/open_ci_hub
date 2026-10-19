import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hub
from providers import github, gitlab, circleci
from providers.common import secret_from, validate_name, RUNNER_TAG


class ValidationTests(unittest.TestCase):
    def test_github_repo(self):
        self.assertEqual(github.validate_target('https://github.com/example/project')[1],
                         'repos/example/project/actions/runners/registration-token')

    def test_github_org(self):
        self.assertEqual(github.validate_target('https://github.com/example')[1],
                         'orgs/example/actions/runners/registration-token')

    def test_bad_github_urls(self):
        for value in ['http://github.com/a/b', 'https://github.com@evil.test/a/b',
                      'https://github.com/a/b/extra', 'https://github.com/a/b?token=secret']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                github.validate_target(value)

    def test_gitlab_project_and_subgroups(self):
        self.assertEqual(gitlab.validate_target('https://gitlab.com/group/sub/project')[2], 'group/sub/project')

    def test_gitlab_invalid(self):
        for value in ['https://gitlab.com/group', 'http://gitlab.com/group/proj',
                      'https://gitlab.com@evil.test/group/proj']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                gitlab.validate_target(value)

    def test_circleci_class(self):
        self.assertEqual(circleci.validate_class('my-org/build:fast'), 'my-org/build:fast')
        for value in ['', 'a b/x', '../../secret', 'a/b\nS=secret']:
            with self.assertRaises(ValueError): circleci.validate_class(value)

    def test_runner_names(self):
        self.assertEqual(validate_name('build_2'), 'build_2')
        for value in ['', 'a b', 'oops\nFOO=bar', '../path']:
            with self.assertRaises(ValueError): validate_name(value)

    def test_token_validation(self):
        self.assertEqual(secret_from({'token': 'hello'}), 'hello')
        for value in [{'token': ''}, {'token': 'line\nbreak'}, {'token': None}, {}]:
            with self.assertRaises(RuntimeError): secret_from(value)

    def test_provider_registry(self):
        self.assertEqual(set(hub.SUPPORTED), {'github', 'gitlab', 'circleci'})
        self.assertEqual(RUNNER_TAG, 'local-ci')

    def test_dashboard_loopback(self):
        import inspect
        import web_console
        self.assertIn("('127.0.0.1', self.port)", inspect.getsource(web_console.WebConsole.serve))

    def test_secret_ignore(self):
        self.assertIn('.local/', (hub.ROOT / '.gitignore').read_text())

    def test_secure_write(self):
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / '.local' / 'token.env'
            hub.secure_write(dest, 'HELLO=secret\n')
            self.assertEqual(dest.read_text(), 'HELLO=secret\n')
            hub.secure_write(dest, 'HELLO=changed\n')
            self.assertEqual(dest.read_text(), 'HELLO=changed\n')
            if sys.platform != 'win32':
                self.assertEqual(dest.stat().st_mode & 0o777, 0o600)

    @patch.object(hub, 'compose')
    @patch.object(hub, 'secure_write')
    def test_github_registration_no_secrets_saved(self, write, compose):
        from providers import Registration
        hub.register('github', Registration('runner', 'https://github.com/a/b', 'SECRET', {'name': 'runner'}))
        saved = ''.join(str(a) for args, _ in write.call_args_list for a in args)
        self.assertNotIn('SECRET', saved)
        first = compose.call_args_list[0]
        self.assertNotIn('SECRET', repr(first.args))
        self.assertEqual(first.kwargs['extra_env']['GH_TOKEN'], 'SECRET')

    @patch.object(hub, 'compose')
    @patch.object(hub, 'secure_write')
    def test_gitlab_registration_uses_environment(self, write, compose):
        from providers import Registration
        hub.register('gitlab', Registration('runner', 'https://gitlab.com', 'SECRET', {'name': 'runner'}))
        self.assertNotIn('SECRET', repr(compose.call_args_list[0].args))
        self.assertEqual(compose.call_args_list[0].kwargs['extra_env']['CI_SERVER_TOKEN'], 'SECRET')

    @patch.object(hub, 'compose')
    @patch.object(hub, 'secure_write')
    def test_circleci_token_saved_locally(self, write, compose):
        from providers import Registration
        hub.register('circleci', Registration('runner', 'org/class', 'SECRET', {'name': 'runner'}))
        self.assertTrue(any('SECRET' in repr(call.args) and 'circleci.env' in repr(call.args)
                            for call in write.call_args_list))

    @patch.object(github, 'response_object', return_value={'token': 'issued'})
    @patch.object(github, 'authenticated', return_value=True)
    @patch('builtins.input', side_effect=['https://github.com/owner/repo', 'local-runner'])
    def test_github_oauth_setup_api(self, _input, _auth, api):
        registration = github.prepare()
        self.assertEqual(registration.token, 'issued')
        self.assertIn('/repos/owner/repo/', api.call_args.args[0][-1])

    @patch.object(gitlab, 'response_object', side_effect=[{'id': 123}, {'token': 'glrt-abc', 'id': 44}])
    @patch.object(gitlab, 'authenticated', return_value=True)
    @patch('builtins.input', side_effect=['https://gitlab.com/myteam/project', 'local-runner'])
    def test_gitlab_create_project_runner(self, _input, _auth, api):
        result = gitlab.prepare()
        self.assertEqual(result.token, 'glrt-abc')
        call = api.call_args_list[-1].args[0]
        self.assertIn('user/runners', call)
        self.assertIn('access_level=ref_protected', call)
        self.assertIn('tag_list=local-ci', call)

    @patch.object(circleci, 'response_object', return_value={'token': 'issued', 'id': '9'})
    @patch.object(circleci, 'authenticated', return_value=True)
    @patch('builtins.input', side_effect=['local-runner', 'org/build', 'n'])
    def test_circleci_issue_token(self, _input, _auth, api):
        self.assertEqual(circleci.prepare().token, 'issued')
        self.assertIn('runner', api.call_args.args[0])


if __name__ == '__main__': unittest.main()
