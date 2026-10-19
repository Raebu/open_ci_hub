import subprocess
import unittest
from unittest.mock import patch
from providers import common, gitlab


class ProviderSafetyTests(unittest.TestCase):
    def test_timeout_does_not_disclose_token_bearing_process(self):
        error = subprocess.TimeoutExpired(['gh', 'SECRET'], 120, output='SECRET', stderr='SECRET')
        with patch.object(common.shutil, 'which', return_value='/bin/gh'), patch.object(common.subprocess, 'run', side_effect=error):
            with self.assertRaises(RuntimeError) as exc:
                common.command(['gh', 'SECRET'], sensitive=True)
            self.assertNotIn('SECRET', str(exc.exception))
            self.assertIn('timed out', str(exc.exception))

    def test_gitlab_https_port_and_typed_server_restrictions(self):
        with patch.object(gitlab, 'authenticated', return_value=True) as auth, patch.object(gitlab, 'response_object', side_effect=[{'id': 7}, {'id': 9, 'token': 'dummy'}]) as api, patch('builtins.input', side_effect=['https://gitlab.example:8443/a/b', 'worker']):
            gitlab.prepare()
        auth.assert_called_once_with('gitlab.example:8443')
        args = api.call_args.args[0]
        self.assertIn('gitlab.example:8443', args)
        for key in ['run_untagged=false', 'project_id=7']:
            self.assertEqual(args[args.index(key) - 1], '-F')
