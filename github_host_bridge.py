#!/usr/bin/env python3
"""Owner-approved GitHub issue -> native Garuda user task bridge.

The GitHub account can REQUEST a task. It cannot AUTHORISE execution:
only the local Linux user can approve an exact request digest. This module
is intentionally separate from CI runners and interactive PTY sessions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from hub import secure_write
from state_lock import setup_lock

ROOT = Path(__file__).resolve().parent
STATE = ROOT / '.local' / 'github-host-bridge'
CONFIG = STATE / 'config.json'
JOBS = STATE / 'jobs'
REPO = re.compile(r'^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$')
LOGIN = re.compile(r'^[A-Za-z0-9-]{1,39}$')
DIGEST = re.compile(r'^[a-f0-9]{64}$')
SCHEMA = 'open-ci-hub.host-task.v1'
MAX_ISSUE_BYTES = 6000
MAX_GH_BYTES = 200000


def gh(*args, method='GET', fields=None):
    """Execute gh with argv only; return JSON, never log token or stderr."""
    command = ['gh', 'api', '--method', method, *args]
    for name, value in (fields or {}).items():
        command.extend(['-f', name + '=' + str(value)])
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=25, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError('GitHub API unavailable; no host task executed.') from None
    if result.returncode or len(result.stdout) > MAX_GH_BYTES:
        raise RuntimeError('GitHub API error or oversized response; no host task executed.')
    try:
        return json.loads(result.stdout)
    except (ValueError, UnicodeError):
        raise RuntimeError('Invalid GitHub API response; no host task executed.') from None


def require_user():
    if os.name != 'posix' or os.geteuid() == 0:
        raise PermissionError('Run the bridge directly as the non-root host user.')


def read_private(path):
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
        raise RuntimeError('Private bridge configuration/state missing or unsafe.')
    if path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o077:
        raise PermissionError('Bridge state must be owned by this user and mode 0600.')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise RuntimeError('Bridge state is corrupt; inspect without overwriting.') from None


def config():
    data = read_private(CONFIG)
    if (not isinstance(data, dict) or not isinstance(data.get('repo'), str)
        or not REPO.fullmatch(data['repo']) or not isinstance(data.get('sender'), str)
        or not LOGIN.fullmatch(data['sender']) or type(data.get('repo_id')) != int):
        raise RuntimeError('Invalid bridge configuration.')
    return data


def configure(repo, sender, *, confirm=False):
    require_user()
    if not confirm or not REPO.fullmatch(repo) or not LOGIN.fullmatch(sender):
        raise PermissionError('Explicit local confirmation, exact repository and GitHub login required.')
    info = gh('repos/' + repo)
    if (not isinstance(info, dict) or info.get('private') is not True
        or str(info.get('full_name', '')).lower() != repo.lower()
        or type(info.get('id')) != int
        or str(info.get('owner', {}).get('login', '')).lower() != sender.lower()):
        raise PermissionError('Control repository must be private and owned by the named sender.')
    if CONFIG.exists():
        old = config()
        if old != {'repo': info['full_name'], 'sender': sender, 'repo_id': info['id']}:
            raise RuntimeError('Already configured; refusing to silently switch trusted repository.')
        return old
    result = {'repo': info['full_name'], 'sender': sender, 'repo_id': info['id']}
    secure_write(CONFIG, json.dumps(result, sort_keys=True) + '\n')
    return result


def validate_task(issue, cfg):
    if (not isinstance(issue, dict) or type(issue.get('number')) != int
        or issue['number'] < 1 or issue.get('state') != 'open'
        or issue.get('pull_request') or
        not isinstance(issue.get('user'), dict) or
        str(issue['user'].get('login', '')).lower() != cfg['sender'].lower()):
        raise PermissionError('Not an open issue authored by the configured GitHub owner.')
    title, body = issue.get('title'), issue.get('body')
    if (not isinstance(title, str) or not title.startswith('[hub-task] ')
        or not isinstance(body, str) or len(body.encode('utf-8')) > MAX_ISSUE_BYTES):
        raise ValueError('Invalid host-task title or body.')
    try:
        task = json.loads(body)
    except ValueError:
        raise ValueError('Host-task body must be a JSON object.') from None
    if not isinstance(task, dict) or set(task) != {'schema', 'description', 'argv', 'cwd', 'timeout'}:
        raise ValueError('Unexpected host-task schema or fields.')
    argv, cwd = task['argv'], task['cwd']
    if (task['schema'] != SCHEMA or not isinstance(task['description'], str)
        or not 1 <= len(task['description']) <= 180
        or not isinstance(argv, list) or not 1 <= len(argv) <= 24
        or any(not isinstance(s, str) or not 1 <= len(s) <= 1024
               or '\x00' in s for s in argv)
        or not argv[0].startswith('/') or '..' in Path(argv[0]).parts
        or not isinstance(cwd, str) or not cwd.startswith('/')
        or '..' in Path(cwd).parts or '\x00' in cwd or len(cwd) > 300
        or type(task['timeout']) != int or not 5 <= task['timeout'] <= 300):
        raise ValueError('Invalid native command, working directory or timeout.')
    # This is NOT a shell sandbox. Full user account access is granted only
    # after reviewing the exact argv and its digest on the host.
    stable = json.dumps({'number': issue['number'], 'author': cfg['sender'].lower(),
                         'title': title, 'body': body}, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(stable.encode('utf-8')).hexdigest()
    return {'number': issue['number'], 'digest': digest, 'task': task}


def get_issue(cfg, number):
    if type(number) != int or not 1 <= number <= 1000000000:
        raise ValueError('Invalid issue number.')
    issue = gh('repos/' + cfg['repo'] + '/issues/' + str(number))
    if not isinstance(issue, dict):
        raise ValueError('Invalid GitHub issue.')
    return validate_task(issue, cfg)


def pending():
    cfg = config()
    issues = gh('repos/' + cfg['repo'] + '/issues', fields={'state': 'open', 'per_page': '100'})
    if not isinstance(issues, list):
        raise ValueError('Invalid GitHub issue collection.')
    items = []
    for item in issues:
        if not isinstance(item, dict) or not str(item.get('title', '')).startswith('[hub-task] '):
            continue
        try:
            request = validate_task(item, cfg)
        except (PermissionError, ValueError):
            continue
        items.append(request)
    return items


def job_path(number):
    return JOBS / (str(number) + '.json')


def approve(number, digest, *, confirm=False):
    require_user()
    if not confirm or not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise PermissionError('Exact SHA-256 digest and local --confirm required.')
    cfg = config()
    issue = get_issue(cfg, number)
    if digest != issue['digest']:
        raise PermissionError('Issue changed or supplied digest does not match.')
    path = job_path(number)
    if path.exists() or path.is_symlink():
        raise RuntimeError('Task already recorded; do not replay an old approval.')
    record = {'issue': number, 'digest': digest, 'repo_id': cfg['repo_id'],
              'status': 'approved', 'approved_at': int(time.time())}
    secure_write(path, json.dumps(record, sort_keys=True) + '\n')
    return {'issue': number, 'status': 'approved', 'expires_in_seconds': 3600}


def safe_capacity():
    from machine_policy import sample, admission
    from resource_guard import memory_available_mb
    policy = admission('quiet', sample(), 1)
    if policy['max_parallel'] < 1:
        raise RuntimeError('Host task deferred due to PC resource pressure: ' + policy['reason'])
    available = memory_available_mb()
    if available is None or available < 5120:
        raise RuntimeError('Host task deferred: insufficient verified desktop RAM headroom.')
    return True


def execute_native(task, number):
    """Native host-user execution, NOT CI/container execution or a PTY mirror."""
    from bounded_output import exchange
    from managed_worker import stop_scope, scope_confirmed_inactive
    argv, cwd = task['argv'], Path(task['cwd'])
    if not cwd.is_dir() or not Path(argv[0]).is_file() or not os.access(argv[0], os.X_OK):
        raise RuntimeError('Approved working directory or executable is unavailable.')
    unit = 'open-ci-app-' + hashlib.sha256(('github-task-' + str(number) +
               str(time.monotonic_ns())).encode()).hexdigest()[:32] + '.scope'
    command = ['/usr/bin/systemd-run', '--user', '--scope', '--quiet', '--collect',
               '--pipe', '--unit=' + unit.removesuffix('.scope'),
               '-p', 'MemoryMax=1024M', '-p', 'MemorySwapMax=0',
               '-p', 'CPUQuota=100%', '-p', 'TasksMax=96', '--', *argv]
    if not Path(command[0]).is_file():
        raise RuntimeError('Systemd user scope unavailable; refusing unbounded host execution.')
    env = {k: v for k, v in os.environ.items()
           if k not in ('GH_TOKEN', 'GITHUB_TOKEN', 'GITHUB_PAT', 'OPENAI_API_KEY',
                        'OPEN_CI_WORKER_TOKEN', 'OPEN_CI_APP_TOKEN')}
    try:
        proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True)
    except OSError:
        raise RuntimeError('Systemd user scope could not start.') from None
    out = exchange(proc, b'', task['timeout'], lambda: stop_scope(unit, proc))
    exit_code = proc.poll()
    if out['reason'] or exit_code is None:
        stop_scope(unit, proc)
    confirmed = scope_confirmed_inactive(unit)
    output = out['output']
    return {'status': ('stranded' if not confirmed else
                       'failed' if out['reason'] or exit_code != 0 else 'completed'),
            'exit_code': exit_code, 'reason': out['reason'],
            'output_sha256': hashlib.sha256(output).hexdigest(),
            'output_preview': output.decode('utf-8', 'replace')[:4096],
            'scope_stopped': confirmed}


def run_once(executor=execute_native):
    require_user()
    cfg = config()
    if not JOBS.exists():
        return {'status': 'idle'}
    # Caller uses setup_lock around the entire operation to prevent a second
    # host bridge process from replaying an approved request.
    for path in sorted(JOBS.glob('*.json'), key=lambda p: int(p.stem) if p.stem.isdecimal() else 0):
        if not path.stem.isdecimal():
            continue
        record = read_private(path)
        if record.get('status') != 'approved':
            continue
        if record.get('repo_id') != cfg['repo_id']:
            raise RuntimeError('Control repository identity changed; execution blocked.')
        if time.time() - record.get('approved_at', 0) > 3600:
            record['status'] = 'expired'
            secure_write(path, json.dumps(record, sort_keys=True) + '\n')
            continue
        request = get_issue(cfg, record['issue'])
        if request['digest'] != record['digest']:
            record['status'] = 'rejected_changed'
            secure_write(path, json.dumps(record, sort_keys=True) + '\n')
            continue
        safe_capacity()
        # Record running BEFORE spawning; after a crash never re-execute it.
        record.update({'status': 'running', 'started_at': int(time.time())})
        secure_write(path, json.dumps(record, sort_keys=True) + '\n')
        try:
            outcome = executor(request['task'], request['number'])
        except (RuntimeError, ValueError, OSError) as exc:
            outcome = {'status': 'stranded', 'reason': type(exc).__name__,
                       'scope_stopped': False}
        record.update({'status': outcome['status'],
                       'ended_at': int(time.time()), 'result': outcome})
        secure_write(path, json.dumps(record, sort_keys=True) + '\n')
        # GitHub receives only status + digest; stdout stays private on the PC.
        comment = ('Open CI Hub host task: ' + record['status'] + '\n'
                   + 'Request SHA-256: ' + record['digest'] + '\n'
                   + 'Exit code: ' + str(outcome.get('exit_code', 'unknown')) + '\n'
                   + 'Output SHA-256: ' + str(outcome.get('output_sha256', 'unavailable')) + '\n'
                   + 'Raw output kept on the host; not sent to GitHub.')
        try:
            gh('repos/' + cfg['repo'] + '/issues/' + str(record['issue']) + '/comments',
               method='POST', fields={'body': comment})
            record['reported'] = True
            secure_write(path, json.dumps(record, sort_keys=True) + '\n')
        except RuntimeError:
            record['reported'] = False
            secure_write(path, json.dumps(record, sort_keys=True) + '\n')
        return {'issue': record['issue'], 'status': record['status'],
                'reported': record['reported']}
    return {'status': 'idle'}


def status():
    config()
    if not JOBS.is_dir():
        return []
    result = []
    for path in sorted(JOBS.glob('*.json')):
        if path.stem.isdecimal():
            row = read_private(path)
            result.append({'issue': row.get('issue'), 'status': row.get('status'),
                           'digest': row.get('digest'),
                           'reported': row.get('reported')})
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest='command', required=True)
    c = commands.add_parser('configure')
    c.add_argument('--repo', required=True)
    c.add_argument('--sender', required=True)
    c.add_argument('--confirm', action='store_true')
    commands.add_parser('pending')
    a = commands.add_parser('approve')
    a.add_argument('number', type=int)
    a.add_argument('--digest', required=True)
    a.add_argument('--confirm', action='store_true')
    commands.add_parser('run-once')
    commands.add_parser('status')
    args = p.parse_args(argv)
    try:
        require_user()
        if args.command == 'configure':
            result = configure(args.repo, args.sender, confirm=args.confirm)
        elif args.command == 'pending':
            result = pending()
        elif args.command == 'approve':
            result = approve(args.number, args.digest, confirm=args.confirm)
        elif args.command == 'run-once':
            with setup_lock(STATE):
                result = run_once()
        else:
            result = status()
        print(json.dumps(result, indent=2))
        return 0
    except (RuntimeError, PermissionError, ValueError, OSError, TypeError) as exc:
        print('GitHub host bridge: ' + str(exc)[:240], file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
