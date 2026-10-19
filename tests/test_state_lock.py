import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from state_lock import setup_lock
import hub


class SetupLockTests(unittest.TestCase):
    def test_second_process_fails_and_lock_releases_after_error(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            script = 'from pathlib import Path; from state_lock import setup_lock; import sys\nwith setup_lock(Path(sys.argv[1])): pass'
            with self.assertRaisesRegex(ValueError, 'simulated'):
                with setup_lock(directory):
                    result = subprocess.run([sys.executable, '-c', script, folder], capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('Another runner setup', result.stderr)
                    raise ValueError('simulated')
            with setup_lock(directory):
                pass
            self.assertTrue((directory / 'runner-setup.lock').exists())

    @unittest.skipIf(os.name == 'nt', 'Symlink creation requires extra Windows privileges')
    def test_symlink_is_rejected_without_writing_target(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            target = directory / 'important'
            target.write_text('untouched')
            (directory / 'runner-setup.lock').symlink_to(target)
            with self.assertRaises(RuntimeError), setup_lock(directory):
                self.fail('must reject symlink')
            self.assertEqual(target.read_text(), 'untouched')

    def test_setup_holds_lock_before_any_registration(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(hub, 'LOCAL', Path(folder)), patch.object(hub, '_setup') as setup:
            with setup_lock(Path(folder)):
                with self.assertRaises(RuntimeError):
                    hub.setup('github')
            setup.assert_not_called()

    def test_github_registration_does_not_replace_existing_runner(self):
        with patch.object(hub, 'compose') as compose, patch.object(hub, 'secure_write'):
            hub.register('github', hub.Registration('worker', 'https://github.com/a/b', 'secret', {}))
            self.assertNotIn('--replace', compose.call_args_list[0].args[-1])

    def test_invalid_metadata_fails_before_docker_registration(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(hub, 'LOCAL', Path(folder)), patch.object(hub, 'compose') as compose:
            (Path(folder) / 'gitlab-runners.json').write_text('[null]')
            with self.assertRaisesRegex(RuntimeError, 'metadata'):
                hub.register('gitlab', hub.Registration('worker', 'https://gitlab.com', 'secret', {}))
            compose.assert_not_called()

    def test_invalid_metadata_fails_before_remote_token_creation(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(hub, 'LOCAL', Path(folder)), patch.object(hub, 'docker_ready', return_value=True), patch.object(hub.ADAPTERS['gitlab'], 'prepare') as prepare:
            (Path(folder) / 'gitlab-runners.json').write_text('corrupt')
            with self.assertRaisesRegex(RuntimeError, 'metadata'):
                hub.setup('gitlab')
            prepare.assert_not_called()
