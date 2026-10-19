"""Interactive terminal wrapper for Open CI Hub provider sign-in and setup.

A graphical terminal often closes when its one-shot command exits. Always keep
it open to show a clear result and allow the user to review failures.
"""
from __future__ import annotations
from pathlib import Path
import shutil
import subprocess
import sys

PROVIDERS = ('github', 'gitlab', 'circleci')
MODES = ('login', 'setup', 'group', 'add-project', 'add-group')


def provider_command(provider: str, mode: str) -> list[str]:
    if provider not in PROVIDERS or mode not in MODES:
        raise ValueError('Unknown provider or operation')
    if mode in ('add-project', 'add-group'):
        if provider != 'gitlab':
            raise ValueError('Only GitLab supports multiple runner registrations.')
        return [sys.executable, str(Path(__file__).resolve().parent / 'hub.py'),
                'add-gitlab-group' if mode == 'add-group' else 'add-gitlab-project']
    if mode == 'group':
        if provider != 'gitlab':
            raise ValueError('Only GitLab supports group-scoped setup.')
        return [sys.executable, str(Path(__file__).resolve().parent / 'hub.py'), 'setup-gitlab-group']
    if mode == 'setup':
        return [sys.executable, str(Path(__file__).resolve().parent / 'hub.py'),
                'setup', provider]
    return {
        'github': ['gh', 'auth', 'login', '--hostname', 'github.com', '--web', '--scopes', 'repo'],
        'gitlab': ['glab', 'auth', 'login', '--hostname', 'gitlab.com', '--web'],
        'circleci': ['circleci', 'auth', 'login'],
    }[provider]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print('Usage: terminal_session.py <github|gitlab|circleci> <login|setup>', flush=True)
        return 2
    try:
        command = provider_command(argv[0], argv[1])
    except ValueError as exc:
        print(f'Error: {exc}', flush=True)
        return 2
    label = f'{argv[0].title()} {"runner setup" if argv[1] == "setup" else "sign-in"}'
    print(f'Open CI Hub — {label}', flush=True)
    print('The terminal will remain open so you can check the outcome.\n', flush=True)
    try:
        if not shutil.which(command[0]):
            print(f'Error: {command[0]} is not installed or not available on PATH.', flush=True)
            code = 127
        else:
            code = subprocess.run(command, check=False).returncode
    except KeyboardInterrupt:
        code = 130
        print('\nSetup interrupted.', flush=True)
    except OSError as exc:
        code = 1
        print(f'Error starting setup: {exc}', flush=True)
    if code == 0:
        print('\nCompleted successfully.', flush=True)
    else:
        print(f'\nFAILED (exit code {code}). Review the messages above.', flush=True)
        print('Do not repeat runner registration blindly; an orphaned registration may exist.', flush=True)
    try:
        input('Press Enter to close this window...')
    except (EOFError, KeyboardInterrupt):
        pass
    return code


if __name__ == '__main__':
    raise SystemExit(main())
