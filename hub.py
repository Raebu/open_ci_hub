#!/usr/bin/env python3
"""Open CI Hub: local management of independent, official self-hosted CI runners.

No provider-specific OAuth application registration is needed for the supported
cloud services. Sign-in is delegated to their official, locally installed CLIs.
All work runs locally; this is NOT a CI SaaS or a universal workflow interpreter.
"""
from __future__ import annotations
import argparse
import html
import json
import os
from pathlib import Path
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import webbrowser
from providers import ADAPTERS, Registration
from providers.common import RUNNER_TAG

ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / '.local'
APP_NAME = 'Open CI Hub'
PROJECT = 'open-ci-hub'
SUPPORTED = tuple(ADAPTERS)
CONTAINERS = {p: f'open-ci-{p}-runner' for p in SUPPORTED}


def run(args: list[str], *, extra_env: dict[str, str] | None = None,
        capture: bool = False, timeout: int = 120):
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(args, cwd=ROOT, env=env, text=True, check=True, timeout=timeout,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None)


def docker_ready() -> bool:
    try:
        run(['docker', 'info'], capture=True, timeout=15)
        run(['docker', 'compose', 'version'], capture=True, timeout=15)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def compose(*args: str, extra_env: dict[str, str] | None = None, capture: bool = False):
    return run(['docker', 'compose', '--project-name', PROJECT, *args], extra_env=extra_env, capture=capture)


def secure_write(path: Path, content: str):
    """Atomic private state file; files and parent must not be symlinks."""
    import tempfile
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise RuntimeError('Refusing to save local state through symlinks.')
    if os.name != 'nt':
        os.chmod(path.parent, 0o700)
    # Never leave partial secrets behind. A different same-filesystem temp name avoids
    # overwriting a symlink supplied by another process.
    fd, temp_path = tempfile.mkstemp(prefix='.hub-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        if os.name != 'nt':
            os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def metadata_path(provider: str) -> Path:
    return LOCAL / f'{provider}.json'


def gitlab_index() -> list:
    index = LOCAL / 'gitlab-runners.json'
    try:
        current = json.loads(index.read_text()) if index.exists() else []
        if not isinstance(current, list) or any(not isinstance(x, dict) for x in current):
            raise RuntimeError('Invalid GitLab runner metadata. Do not overwrite it.')
        if not current and metadata_path('gitlab').exists():
            current = [json.loads(metadata_path('gitlab').read_text())]
    except (OSError, ValueError):
        raise RuntimeError('Cannot read existing GitLab runner metadata safely; repair it before registration.') from None
    if not isinstance(current, list) or any(not isinstance(x, dict) for x in current):
        raise RuntimeError('Invalid GitLab runner metadata. Do not overwrite it.')
    return current


def register(provider: str, data: Registration) -> None:
    """Register the native runner using tokens only in transient process environment."""
    if provider == 'gitlab':
        current = gitlab_index()
        if any(x.get('runner_id') == data.metadata.get('runner_id') and data.metadata.get('runner_id') for x in current):
            raise RuntimeError('This GitLab runner already appears in the local index.')
    if provider == 'github':
        # Never embed the token in the host-side Docker argv; expand inside the
        # short-lived container. Docker administrators can still inspect it.
        command = ('exec ./config.sh --unattended --url "$GH_URL" '
                   '--token "$GH_TOKEN" --name "$GH_NAME" '
                   f'--labels {RUNNER_TAG} --work _work')
        compose('--profile', 'github', 'run', '--rm', '--no-deps',
                '-e', 'GH_URL', '-e', 'GH_NAME', '-e', 'GH_TOKEN',
                'github', '/bin/sh', '-c', command,
                extra_env={'GH_URL': data.target, 'GH_NAME': data.name, 'GH_TOKEN': data.token})
    elif provider == 'gitlab':
        compose('--profile', 'gitlab', 'run', '--rm', '--no-deps',
                '-e', 'REGISTER_NON_INTERACTIVE', '-e', 'CI_SERVER_URL',
                '-e', 'RUNNER_NAME', '-e', 'CI_SERVER_TOKEN', '-e', 'RUNNER_EXECUTOR',
                'gitlab', 'register',
                extra_env={'REGISTER_NON_INTERACTIVE': 'true', 'CI_SERVER_URL': data.target,
                           'RUNNER_NAME': data.name, 'CI_SERVER_TOKEN': data.token,
                           'RUNNER_EXECUTOR': 'shell'})
    elif provider == 'circleci':
        # The CircleCI machine agent needs its provider-issued credential after
        # restart. Store it outside version control; generate it through the CLI.
        # CircleCI runner name may not contain line breaks thanks to validation.
        secure_write(LOCAL / 'circleci.env',
                     f'CIRCLECI_RUNNER_NAME={data.name}\n'
                     f'CIRCLECI_RUNNER_API_AUTH_TOKEN={data.token}\n')
    else:
        raise ValueError(f'Unsupported provider: {provider}')
    if provider == 'gitlab':
        index = LOCAL / 'gitlab-runners.json'
        current.append(data.metadata)
        secure_write(index, json.dumps(current, indent=2) + '\n')
        if not metadata_path(provider).exists():
            secure_write(metadata_path(provider), json.dumps(data.metadata, indent=2) + '\n')
    else:
        secure_write(metadata_path(provider), json.dumps(data.metadata, indent=2) + '\n')
    compose('--profile', provider, 'up', '-d', provider)


def connect(provider: str) -> None:
    ADAPTERS[provider].login()
    print(f'{provider.title()} account connected using official CLI credentials.')
    print(f'Next: python hub.py setup {provider}')


def setup(provider: str, *, group: bool = False, add: bool = False, target: str | None = None, name: str | None = None) -> None:
    from state_lock import setup_lock
    with setup_lock(LOCAL):
        _setup(provider, group=group, add=add, target=target, name=name)


def _setup(provider: str, *, group: bool = False, add: bool = False, target: str | None = None, name: str | None = None) -> None:
    if not docker_ready():
        raise RuntimeError('Docker Engine/Desktop and Docker Compose V2 must be running.')
    if metadata_path(provider).exists() and not (provider == 'gitlab' and add):
        raise RuntimeError(f'{provider} is already configured on this installation; '
                           'remove or deregister existing runner before reconfiguration.')
    if group and provider != 'gitlab':
        raise ValueError('Group runner setup is supported for GitLab only.')
    if provider == 'gitlab':
        gitlab_index()  # Fail before creating a remote credential if recovery is needed.
    if target is not None:
        if provider != 'github' or group or add:
            raise ValueError('Noninteractive --target is currently supported for GitHub only.')
        from providers.github import prepare_target
        data = prepare_target(target, name or 'local-ci-runner')
    elif group:
        from providers.gitlab import prepare_group
        data = prepare_group()
    else:
        data = ADAPTERS[provider].prepare()
    try:
        register(provider, data)
    except subprocess.CalledProcessError:
        # Registration may have consumed a token; do not expose token or provider output.
        raise RuntimeError(f'{provider} runner registration/start failed. Review Docker configuration; '
                           'deregister any orphaned runner on the provider website if needed.') from None
    print(f'{provider.title()} runner configured. Tag/label: {RUNNER_TAG}.')
    if provider == 'gitlab':
        print('This project runner accepts tagged jobs on protected refs only.')
    if provider == 'circleci':
        print('Runner agent token is stored privately in .local/circleci.env.')


def runner_state(provider: str) -> str:
    try:
        process = run(['docker', 'ps', '-a', '--filter',
                       f'name=^/{CONTAINERS[provider]}$', '--format', '{{.Status}}'], capture=True)
        return process.stdout.strip() or 'Not started'
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return 'Unavailable'


def status() -> None:
    if not docker_ready():
        print('Docker is unavailable. Start Docker Desktop/Engine.')
        return
    for provider in SUPPORTED:
        print(f'{provider.title():10} {runner_state(provider)}')


def doctor() -> None:
    print(f'{APP_NAME}: system check')
    print('Python:', sys.version.split()[0])
    print('Docker/Compose:', 'READY' if docker_ready() else 'NOT READY')
    for provider, binary in [('github', 'gh'), ('gitlab', 'glab'), ('circleci', 'circleci')]:
        import shutil
        print(f'{provider.title():10} CLI:', 'INSTALLED' if shutil.which(binary) else 'MISSING')
    print('State directory:', LOCAL)
    if docker_ready():
        status()


def provider_action(provider: str, action: str) -> None:
    if not docker_ready():
        raise RuntimeError('Docker is unavailable.')
    if action == 'start':
        if not metadata_path(provider).is_file():
            raise RuntimeError(f'Set up {provider} first.')
        if provider == 'circleci' and not (LOCAL / 'circleci.env').is_file():
            raise RuntimeError('CircleCI agent token not found; reconnect the provider.')
        if 'Up' not in runner_state(provider):
            from native_runner_observer import require_start_capacity
            require_start_capacity(2048)
        compose('--profile', provider, 'up', '-d', provider)
    elif action == 'stop':
        compose('--profile', provider, 'stop', provider)
    elif action == 'logs':
        compose('--profile', provider, 'logs', '--tail', '100', provider)
    else:
        raise ValueError('Unsupported action.')


def dashboard(port: int = 8765, *, open_browser: bool = True) -> None:
    """Serve the local, authenticated, mobile-capable management dashboard."""
    from web_console import WebConsole
    WebConsole(ROOT, port).serve(open_browser=open_browser)


def mobile(action: str, port: int = 8765) -> None:
    from web_console import mobile_enable, mobile_disable
    if action == 'on':
        mobile_enable(port)
    else:
        mobile_disable()


def menu() -> None:
    choices = {'1': ('doctor', None), '2': ('setup', 'github'), '3': ('setup', 'gitlab'),
               '4': ('setup', 'circleci'), '5': ('status', None), '6': ('dashboard', None),
               '7': ('connect', 'github'), '8': ('connect', 'gitlab'),
               '9': ('connect', 'circleci'), '10': ('mobile-on', None), '11': ('mobile-off', None)}
    while True:
        print('\nOPEN CI HUB\n 1. Check PC requirements\n 2. GitHub - sign in and add runner'
              '\n 3. GitLab - sign in and add runner\n 4. CircleCI - sign in and add runner'
              '\n 5. Runner status\n 6. Local dashboard'
              '\n 7. Sign in with GitHub only\n 8. Sign in with GitLab only'
              '\n 9. Sign in with CircleCI only\n 10. Enable private mobile access\n 11. Disable mobile access\n 0. Exit')
        chosen = input('Choose: ').strip()
        if chosen == '0':
            return
        if chosen not in choices:
            print('Unknown option.'); continue
        action, provider = choices[chosen]
        try:
            if action == 'doctor': doctor()
            elif action == 'setup': setup(provider)
            elif action == 'connect': connect(provider)
            elif action == 'status': status()
            elif action == 'dashboard': dashboard()
            elif action == 'mobile-on': mobile('on')
            elif action == 'mobile-off': mobile('off')
        except (RuntimeError, ValueError, FileNotFoundError, subprocess.CalledProcessError) as exc:
            print('Error:', exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Open-source local CI runner orchestrator')
    sub = parser.add_subparsers(dest='command', required=True)
    for operation in ('menu', 'doctor', 'status'):
        sub.add_parser(operation)
    dash = sub.add_parser('dashboard')
    dash.add_argument('--port', type=int, default=8765)
    dash.add_argument('--no-browser', action='store_true')
    sub.add_parser('open', help='Open the private local owner dashboard link')
    for operation in ('connect', 'setup', 'start', 'stop', 'logs'):
        operation_parser = sub.add_parser(operation)
        operation_parser.add_argument('provider', choices=SUPPORTED)
        if operation == 'setup':
            operation_parser.add_argument('--target', help='GitHub HTTPS repository or organisation URL')
            operation_parser.add_argument('--name', help='Native runner name')
    sub.add_parser('setup-gitlab-group', help='Register a protected group-scoped GitLab runner')
    sub.add_parser('add-gitlab-project', help='Register another project-scoped GitLab runner')
    sub.add_parser('add-gitlab-group', help='Register another group-scoped GitLab runner')
    verify_cmd = sub.add_parser('verify-provider', help='Read-only native worker eligibility check')
    verify_cmd.add_argument('provider', choices=SUPPORTED)
    verify_cmd.add_argument('repo')
    verify_cmd.add_argument('--branch', default='main')
    verify_cmd.add_argument('--resource-class', default='')
    mobile_cmd = sub.add_parser('mobile', help='Private Tailscale mobile access')
    mobile_cmd.add_argument('action', choices=('on', 'off'))
    args = parser.parse_args(argv)
    try:
        if args.command == 'verify-provider':
            from onboarding import make_plan
            from readiness import check
            plan = make_plan(args.provider, args.repo, branch=args.branch, resource_class=args.resource_class)
            result = check(plan.provider, plan.repo, plan.branch, args.resource_class)
            print(json.dumps(result, indent=2))
            return 0 if result['verified'] else 2
        elif args.command == 'menu': menu()
        elif args.command == 'doctor': doctor()
        elif args.command == 'status': status()
        elif args.command == 'dashboard': dashboard(args.port, open_browser=not args.no_browser)
        elif args.command == 'open':
            secret_path = LOCAL / 'dashboard-secret'
            if not secret_path.is_file() or secret_path.is_symlink():
                raise RuntimeError('Start the dashboard first.')
            secret = secret_path.read_text().strip()
            webbrowser.open('http://127.0.0.1:8765/#access=' + secret)
            print('Opened the private local dashboard link in your browser.')
        elif args.command == 'mobile': mobile(args.action)
        elif args.command == 'connect': connect(args.provider)
        elif args.command == 'setup': setup(args.provider, target=args.target, name=args.name)
        elif args.command == 'setup-gitlab-group': setup('gitlab', group=True)
        elif args.command == 'add-gitlab-project': setup('gitlab', add=True)
        elif args.command == 'add-gitlab-group': setup('gitlab', group=True, add=True)
        else: provider_action(args.provider, args.command)
        return 0
    except (RuntimeError, ValueError, FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
