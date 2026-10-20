#!/usr/bin/env python3
"""GitHub -> native Garuda host jobs with standing policy for safe tasks.

A private GitHub issue is an *account-authenticated request*, not cryptographic
proof of a human ChatGPT instruction. Only narrowly typed, non-destructive
operations receive standing approval; arbitrary argv requires owner approval.
The bridge remains separate from CI containers and interactive PTYs.
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
TRUSTED_REPO = 'Raebu/open-ci-chatgpt-control'
TRUSTED_REPO_ID = 1427302798
DOCUMENT = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\.(?:txt|md)$')
ROUTINE_FIELDS = {'schema', 'description', 'operation', 'filename', 'content'}
MAX_ROUTINE_CONTENT = 4000
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
        or not LOGIN.fullmatch(data['sender'])
        or data['repo'].lower() != TRUSTED_REPO.lower()
        or data['sender'].lower() != 'raebu'
        or data.get('repo_id') != TRUSTED_REPO_ID
        or type(data.get('repo_id')) != int
        or type(data.get('configured_at')) != int or data['configured_at'] <= 0):
        raise RuntimeError('Invalid bridge configuration.')
    return data


def configure(repo, sender, *, confirm=False):
    require_user()
    if (not confirm or not REPO.fullmatch(repo) or not LOGIN.fullmatch(sender)
        or repo.lower() != TRUSTED_REPO.lower() or sender.lower() != 'raebu'):
        raise PermissionError('Explicit local confirmation, exact repository and GitHub login required.')
    info = gh('repos/' + repo)
    if (not isinstance(info, dict) or info.get('private') is not True
        or str(info.get('full_name', '')).lower() != repo.lower()
        or type(info.get('id')) != int or info['id'] != TRUSTED_REPO_ID
        or str(info.get('owner', {}).get('login', '')).lower() != sender.lower()):
        raise PermissionError('Control repository must be private and owned by the named sender.')
    if CONFIG.exists():
        old = config()
        if any(old.get(k) != v for k, v in
               {'repo': info['full_name'], 'sender': sender, 'repo_id': info['id']}.items()):
            raise RuntimeError('Already configured; refusing to silently switch trusted repository.')
        return old
    result = {'repo': info['full_name'], 'sender': sender, 'repo_id': info['id'],
              'configured_at': int(time.time())}
    secure_write(CONFIG, json.dumps(result, sort_keys=True) + '\n')
    return result



def verify_control_repo(cfg):
    """Fail closed if the private repo is transferred, replaced or exposed."""
    info = gh('repos/' + cfg['repo'])
    if (not isinstance(info, dict) or info.get('private') is not True
        or cfg['repo'].lower() != TRUSTED_REPO.lower()
        or cfg['repo_id'] != TRUSTED_REPO_ID
        or info.get('id') != cfg['repo_id']
        or str(info.get('full_name', '')).lower() != cfg['repo'].lower()
        or str(info.get('owner', {}).get('login', '')).lower() != cfg['sender'].lower()):
        raise PermissionError('Trusted private GitHub control repo is missing, changed or public.')
    return True


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
    if (not isinstance(task, dict) or task.get('schema') != SCHEMA
        or not isinstance(task.get('description'), str)
        or not 1 <= len(task['description']) <= 180):
        raise ValueError('Invalid host-task metadata.')
    if set(task) == ROUTINE_FIELDS | {'attestation'}:
        if (task['operation'] != 'create_document'
            or not isinstance(task['filename'], str)
            or not DOCUMENT.fullmatch(task['filename'])
            or not isinstance(task['content'], str)
            or not 1 <= len(task['content'].encode('utf-8')) <= MAX_ROUTINE_CONTENT
            or any(ord(ch) == 0 for ch in task['content'])):
            raise ValueError('Only bounded create_document is preauthorised.')
    elif set(task) == {'schema', 'description', 'argv', 'cwd', 'timeout'}:
        argv, cwd = task['argv'], task['cwd']
        if (not isinstance(argv, list) or not 1 <= len(argv) <= 24
            or any(not isinstance(a, str) or not 1 <= len(a) <= 1024
                   or '\x00' in a for a in argv)
            or not argv[0].startswith('/') or '..' in Path(argv[0]).parts
            or not isinstance(cwd, str) or not cwd.startswith('/')
            or '..' in Path(cwd).parts or '\x00' in cwd or len(cwd) > 300
            or type(task['timeout']) != int or not 5 <= task['timeout'] <= 300):
            raise ValueError('Invalid native command, working directory or timeout.')
        # Arbitrary commands are NEVER standing-approved based on issue authorship.
    else:
        raise ValueError('Unexpected host-task schema or fields.')
    stable = json.dumps({'number': issue['number'], 'author': cfg['sender'].lower(),
                         'title': title, 'body': body}, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(stable.encode('utf-8')).hexdigest()
    return {'number': issue['number'], 'digest': digest, 'task': task,
            'created_at': issue.get('created_at')}


def get_issue(cfg, number):
    if type(number) != int or not 1 <= number <= 1000000000:
        raise ValueError('Invalid issue number.')
    issue = gh('repos/' + cfg['repo'] + '/issues/' + str(number))
    if not isinstance(issue, dict):
        raise ValueError('Invalid GitHub issue.')
    return validate_task(issue, cfg)


def pending():
    cfg = config()
    verify_control_repo(cfg)
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
    verify_control_repo(cfg)
    issue = get_issue(cfg, number)
    if set(issue['task']) == ROUTINE_FIELDS | {'attestation'}:
        raise PermissionError('Routine document tasks use standing policy, not shell approval.')
    if digest != issue['digest']:
        raise PermissionError('Issue changed or supplied digest does not match.')
    path = job_path(number)
    if path.exists() or path.is_symlink():
        raise RuntimeError('Task already recorded; do not replay an old approval.')
    record = {'issue': number, 'digest': digest, 'repo_id': cfg['repo_id'],
              'status': 'approved', 'approved_at': int(time.time())}
    secure_write(path, json.dumps(record, sort_keys=True) + '\n')
    return {'issue': number, 'status': 'approved', 'expires_in_seconds': 3600}



def is_routine(request):
    return set(request['task']) == ROUTINE_FIELDS | {'attestation'}


def recent_after_configuration(request, cfg):
    """Never run issues that predate explicit host enrolment of a control repo."""
    from datetime import datetime, timezone
    try:
        created = datetime.fromisoformat(request['created_at'].replace('Z', '+00:00'))
        epoch = created.timestamp()
    except (AttributeError, ValueError, TypeError, OverflowError):
        return False
    return cfg['configured_at'] - 60 <= epoch <= time.time() + 60


def stage_standing_requests(cfg):
    """No arbitrary shell job ever enters this branch; cap discovery volume."""
    from request_attestation import verify_attestation
    items = pending()
    count = 0
    used_jtis = set()
    if JOBS.is_dir():
        for recorded in JOBS.glob('*.json'):
            if recorded.stem.isdecimal():
                existing = read_private(recorded)
                if isinstance(existing.get('attestation_jti'), str):
                    used_jtis.add(existing['attestation_jti'])
    for request in items:
        if not is_routine(request) or not recent_after_configuration(request, cfg):
            continue
        try:
            verified = verify_attestation(request['task'])
        except (PermissionError, ValueError):
            continue
        if verified['jti'] in used_jtis:
            continue
        path = job_path(request['number'])
        if path.exists() or path.is_symlink():
            continue
        record = {'issue': request['number'], 'digest': request['digest'],
                  'repo_id': cfg['repo_id'], 'status': 'approved',
                  'approved_at': int(time.time()),
                  'authorisation': 'standing_create_document',
                  'attestation_jti': verified['jti'],
                  'attestation_subject': verified['subject']}
        # The caller holds the cross-process lock. Write exactly once.
        secure_write(path, json.dumps(record, sort_keys=True) + '\n')
        used_jtis.add(verified['jti'])
        count += 1
        if count == 10:
            break
    return count


def create_document(task, number):
    """Create-only native filesystem operation, no subprocess or shell involved."""
    if set(task) != ROUTINE_FIELDS | {'attestation'}:
        raise PermissionError('Not an approved routine document operation.')
    # Revalidate at use time, independent of the GitHub JSON parser.
    if (task['operation'] != 'create_document'
        or not DOCUMENT.fullmatch(task['filename'])
        or not isinstance(task['content'], str)
        or len(task['content'].encode('utf-8')) > MAX_ROUTINE_CONTENT):
        raise ValueError('Unsafe standing document request.')
    directory = Path.home() / 'Documents' / 'OpenCIHubTasks'
    if directory.is_symlink() or directory.parent.is_symlink():
        raise RuntimeError('Refusing symlinked document directory.')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or directory.stat().st_uid != os.geteuid():
        raise RuntimeError('Unsafe document output directory.')
    path = directory / task['filename']
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write(task['content'].encode('utf-8'))
            file.flush()
            os.fsync(file.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return {'status': 'completed', 'exit_code': 0,
            'output_sha256': hashlib.sha256(task['content'].encode('utf-8')).hexdigest(),
            'scope_stopped': True, 'file_name': task['filename']}


def safe_capacity():
    from machine_policy import sample, admission
    from app_runtime import Broker
    sensors = sample()
    # On this Garuda PC the CPU package sensor is available. An unexpected
    # loss of this critical telemetry must not silently unlock host tasks.
    if sensors.get('temperature_c') is None:
        raise RuntimeError('Host task deferred: CPU thermal sensor unavailable.')
    policy = admission('quiet', sensors, 1)
    if policy['max_parallel'] < 1:
        raise RuntimeError('Host task deferred due to PC resource pressure: ' + policy['reason'])
    resources = Broker().resource_status()
    runtime_policy = resources.get('policy', {})
    if runtime_policy.get('paused') or runtime_policy.get('max_parallel') == 0:
        raise RuntimeError('Host task deferred: Open CI Hub admissions paused.')
    available = resources.get('memory_available_mb')
    native = resources.get('reserved_native_runner_ram_mb')
    active = resources.get('reserved_ram_mb')
    minimum = resources.get('capacity', {}).get('min_free_ram_mb')
    if any(type(v) != int or v < 0 for v in (available, native, active, minimum)):
        raise RuntimeError('Host task deferred: native runner or RAM telemetry unavailable.')
    if available - native - active - minimum < 1024:
        raise RuntimeError('Host task deferred: cannot reserve 1 GiB while protecting desktop and CI.')
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


def run_once(executor=execute_native, document_writer=create_document):
    require_user()
    cfg = config()
    verify_control_repo(cfg)
    stage_standing_requests(cfg)
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
        if record.get('authorisation') == 'standing_create_document':
            from request_attestation import verify_attestation
            try:
                verified = verify_attestation(request['task'])
            except (PermissionError, ValueError):
                verified = None
            if (not verified or verified['jti'] != record.get('attestation_jti')
                or verified['subject'] != record.get('attestation_subject')
                or not is_routine(request) or not recent_after_configuration(request, cfg)):
                record['status'] = 'rejected_policy'
                secure_write(path, json.dumps(record, sort_keys=True) + '\n')
                continue
        safe_capacity()
        # Record running BEFORE spawning; after a crash never re-execute it.
        record.update({'status': 'running', 'started_at': int(time.time())})
        secure_write(path, json.dumps(record, sort_keys=True) + '\n')
        try:
            outcome = (document_writer(request['task'], request['number'])
                       if record.get('authorisation') == 'standing_create_document'
                       else executor(request['task'], request['number']))
        except (RuntimeError, ValueError, OSError, PermissionError, KeyError) as exc:
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
    signer = commands.add_parser('trust-issuer')
    signer.add_argument('--issuer', required=True)
    signer.add_argument('--subject', required=True)
    signer.add_argument('--public-key', required=True)
    signer.add_argument('--confirm', action='store_true')
    args = p.parse_args(argv)
    try:
        require_user()
        if args.command == 'configure':
            result = configure(args.repo, args.sender, confirm=args.confirm)
        elif args.command == 'pending':
            result = pending()
        elif args.command == 'approve':
            result = approve(args.number, args.digest, confirm=args.confirm)
        elif args.command == 'trust-issuer':
            from request_attestation import pin_signer
            result = pin_signer(args.issuer, args.subject, args.public_key,
                                confirm=args.confirm)
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
)
ROUTINE_FIELDS = {'schema', 'description', 'operation', 'filename', 'content'}
MAX_ROUTINE_CONTENT = 4000
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



def verify_control_repo(cfg):
    """Fail closed if the private repo is transferred, replaced or exposed."""
    info = gh('repos/' + cfg['repo'])
    if (not isinstance(info, dict) or info.get('private') is not True
        or info.get('id') != cfg['repo_id']
        or str(info.get('full_name', '')).lower() != cfg['repo'].lower()
        or str(info.get('owner', {}).get('login', '')).lower() != cfg['sender'].lower()):
        raise PermissionError('Trusted private GitHub control repo is missing, changed or public.')
    return True


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
    verify_control_repo(cfg)
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
    verify_control_repo(cfg)
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
    from app_runtime import Broker
    sensors = sample()
    # On this Garuda PC the CPU package sensor is available. An unexpected
    # loss of this critical telemetry must not silently unlock host tasks.
    if sensors.get('temperature_c') is None:
        raise RuntimeError('Host task deferred: CPU thermal sensor unavailable.')
    policy = admission('quiet', sensors, 1)
    if policy['max_parallel'] < 1:
        raise RuntimeError('Host task deferred due to PC resource pressure: ' + policy['reason'])
    resources = Broker().resource_status()
    runtime_policy = resources.get('policy', {})
    if runtime_policy.get('paused') or runtime_policy.get('max_parallel') == 0:
        raise RuntimeError('Host task deferred: Open CI Hub admissions paused.')
    available = resources.get('memory_available_mb')
    native = resources.get('reserved_native_runner_ram_mb')
    active = resources.get('reserved_ram_mb')
    minimum = resources.get('capacity', {}).get('min_free_ram_mb')
    if any(type(v) != int or v < 0 for v in (available, native, active, minimum)):
        raise RuntimeError('Host task deferred: native runner or RAM telemetry unavailable.')
    if available - native - active - minimum < 1024:
        raise RuntimeError('Host task deferred: cannot reserve 1 GiB while protecting desktop and CI.')
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
    verify_control_repo(cfg)
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
