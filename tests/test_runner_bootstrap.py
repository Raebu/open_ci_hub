"""Guard against mounting a blank volume over the GitHub runner distribution."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class GithubRunnerBootstrapTests(unittest.TestCase):
    def test_volume_has_bootstrap_image(self):
        config = (ROOT / 'docker-compose.yml').read_text()
        github = config.split('  github:', 1)[1].split('  gitlab:', 1)[0]
        self.assertIn('dockerfile: containers/github/Dockerfile', github)
        self.assertIn('github-runner-state:/home/runner', github)
        self.assertTrue((ROOT / 'containers/github/bootstrap.sh').exists())

    def test_bootstrap_persists_registration(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source, dest = base / 'assets', base / 'runner'
            source.mkdir(); dest.mkdir()
            for name in ('run.sh', 'config.sh'):
                (source / name).write_text(name)
            env = dict(os.environ, OPEN_CI_RUNNER_HOME=str(dest), OPEN_CI_RUNNER_ASSETS=str(source))
            script = ROOT / 'containers/github/bootstrap.sh'
            subprocess.run(['sh', str(script), 'sh', '-c', 'test -f run.sh && test -f config.sh'],
                           env=env, check=True)
            (dest / '.runner').write_text('private-registration')
            (source / 'run.sh').write_text('changed')
            subprocess.run(['sh', str(script), 'sh', '-c', 'test -f .runner'], env=env, check=True)
            self.assertEqual((dest / '.runner').read_text(), 'private-registration')
            self.assertEqual((dest / 'run.sh').read_text(), 'run.sh')

    def test_registration_passes_through_bootstrap(self):
        from hub import Registration, register
        meta = Registration(name='local-runner', target='https://github.com/test/repo', token='dummy', metadata={})
        with patch('hub.compose') as composer, patch('hub.secure_write'):
            register('github', meta)
        args = composer.call_args_list[0].args
        self.assertNotIn('--entrypoint', args)
        self.assertIn('github', args)
        self.assertIn('/bin/sh', args)


if __name__ == '__main__':
    unittest.main()
