#!/usr/bin/env python3
"""Local validation; never schedules provider workflows or touches existing volumes."""
import argparse
import compileall
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]


def run(label, argv):
    print(f'\nCHECK: {label}', flush=True)
    result = subprocess.run(argv, cwd=ROOT, check=False)
    if result.returncode:
        raise RuntimeError(f'{label} failed (exit {result.returncode}).')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--containers', action='store_true', help='Build runner images and smoke-test GitHub bootstrap using a temporary private volume; requires image downloads')
    args = parser.parse_args()
    try:
        sources = list(ROOT.glob('*.py')) + list((ROOT / 'providers').glob('*.py')) + list((ROOT / 'scripts').glob('*.py')) + list((ROOT / 'tests').glob('*.py'))
        if not all(compileall.compile_file(str(path), quiet=1) for path in sources):
            raise RuntimeError('Python compilation failed.')
        run('Python unit/API/security tests', [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'])
        for binary in ('node', 'docker'):
            if not shutil.which(binary):
                raise RuntimeError(f'Install {binary} to complete validation.')
        run('Dashboard JavaScript syntax', ['node', '--check', 'dashboard/app.js'])
        run('Personal WhatsApp JavaScript syntax', ['node', '--check', 'dashboard/whatsapp-personal.js'])
        run('Personal WhatsApp writing/link tests', ['node', '--test', 'tests/test_whatsapp_personal.cjs'])
        run('Background browser worker syntax', ['node', '--check', 'integrations/background-browser/browser.mjs'])
        run('All Compose profiles (no secret file resolution)', ['docker', 'compose', '--profile', '*', 'config', '--no-env-resolution', '-q'])
        if args.containers:
            suffix = uuid.uuid4().hex[:12]
            image = 'open-ci-hub-validation:github-' + suffix
            gitlab = 'open-ci-hub-validation:gitlab-' + suffix
            volume = 'open-ci-hub-validation-' + suffix
            run('Build GitHub image', ['docker', 'build', '-t', image, '-f', 'containers/github/Dockerfile', '.'])
            run('Build GitLab image', ['docker', 'build', '-t', gitlab, '-f', 'containers/gitlab/Dockerfile', '.'])
            run('Create isolated validation volume', ['docker', 'volume', 'create', volume])
            try:
                options = ['docker', 'run', '--rm', '--network', 'none', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '-v', volume + ':/home/runner', image, 'sh', '-c']
                run('Initial bootstrap', options + ['test -x config.sh && test -x run.sh && printf validation > .bootstrap-validation'])
                run('Restart retains state', options + ['test "$(cat .bootstrap-validation)" = validation && test -x config.sh && test -x run.sh'])
            finally:
                run('Remove only temporary validation volume', ['docker', 'volume', 'rm', volume])
        print('\nPASS: local checks completed. Native push builds, provider artifacts and hardware/mobile recovery remain UNVERIFIED.', flush=True)
        return 0
    except (RuntimeError, OSError) as exc:
        print(f'\nFAIL: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
