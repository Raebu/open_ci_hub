"""Interactive terminal setup must preserve errors until the user dismisses it."""
from __future__ import annotations
import io
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import terminal_session


class TerminalSessionTests(unittest.TestCase):
    def test_allowed_commands_only(self):
        self.assertEqual(terminal_session.provider_command('gitlab', 'login')[0], 'glab')
        self.assertEqual(terminal_session.provider_command('github', 'setup')[-2:], ['setup', 'github'])
        for provider, mode in [('evil', 'setup'), ('github', 'delete')]:
            with self.assertRaises(ValueError):
                terminal_session.provider_command(provider, mode)

    def test_failed_setup_waits_for_user_and_returns_failure(self):
        out = io.StringIO()
        with patch('terminal_session.shutil.which', return_value='/usr/bin/python3'), \
             patch('terminal_session.subprocess.run', return_value=SimpleNamespace(returncode=42)) as run, \
             patch('builtins.input', return_value='') as prompt, \
             patch('sys.stdout', out):
            self.assertEqual(terminal_session.main(['gitlab', 'setup']), 42)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(prompt.call_count, 1)
        self.assertIn('FAILED (exit code 42)', out.getvalue())

    def test_success_waits_for_user(self):
        out = io.StringIO()
        with patch('terminal_session.shutil.which', return_value='/usr/bin/gh'), \
             patch('terminal_session.subprocess.run', return_value=SimpleNamespace(returncode=0)), \
             patch('builtins.input', return_value='') as prompt, \
             patch('sys.stdout', out):
            self.assertEqual(terminal_session.main(['github', 'login']), 0)
        self.assertEqual(prompt.call_count, 1)
        self.assertIn('Completed successfully', out.getvalue())

    def test_missing_cli_stays_visible(self):
        out = io.StringIO()
        with patch('terminal_session.shutil.which', return_value=None), \
             patch('builtins.input', return_value='') as prompt, \
             patch('sys.stdout', out):
            self.assertEqual(terminal_session.main(['circleci', 'login']), 127)
        self.assertEqual(prompt.call_count, 1)
        self.assertIn('not installed', out.getvalue())


if __name__ == '__main__':
    unittest.main()
