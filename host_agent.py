#!/usr/bin/env python3
"""Open CI Hub host agent: local UNIX socket, deny-by-default approved user actions.

This deliberately does not grant CI runners access to the host. It runs as a
regular Linux desktop user; never as root, never listens on TCP, and never
executes arbitrary commands, shell strings, or repository-provided instructions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import socket
import socketserver
import stat
import struct
import subprocess
import sys
import threading
import time

from hub import secure_write

ROOT = Path(__file__).resolve().parent
STATE = ROOT / '.local'
SOCKET = STATE / 'host-agent.sock'
POLICY = STATE / 'host-agent-policy.json'
AUDIT = STATE / 'host-agent-audit.jsonl'
MAX_BYTES = 4096
MAX_PENDING = 256
PREVIEW_TTL = 120
FLATPAK_ID = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{1,60}(?:\.[A-Za-z][A-Za-z0-9_-]{0,60}){2,6}$')
SERVICE_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.@-]{0,100}\.service$')
IDENTITY = re.compile(r'^[a-zA-Z0-9._%+-]{1,90}@[a-zA-Z0-9.-]{1,190}$')
KINDS = {'flatpak_install', 'service_start', 'service_stop', 'service_restart'}
EXEC_TIMEOUT = 180

def policy_data():
    if POLICY.is_symlink() or POLICY.parent.is_symlink():
        raise RuntimeError('Refusing symlinked host policy.')
    if not POLICY.is_file():
        return {'flatpaks': [], 'services': [], 'mobile_admins': []}
    try:
        data = json.loads(POLICY.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        raise RuntimeError('Host agent policy unreadable; administration disabled.') from None
    if not isinstance(data, dict):
        raise RuntimeError('Host agent policy must be an object.')
    for key, pattern in [('flatpaks', FLATPAK_ID), ('services', SERVICE_NAME),
                         ('mobile_admins', IDENTITY)]:
        values = data.get(key)
        if not isinstance(values, list) or len(values) > 100 or any(
                not isinstance(value, str) or not pattern.fullmatch(value) for value in values):
            raise RuntimeError('Invalid host policy section: ' + key)
    if 'open-ci-hub-host-agent.service' in data['services']:
        raise RuntimeError('Host agent cannot administer its own systemd service.')
    return data

def allow(kind: str, value: str, *, confirm: bool = False):
    choices = {'flatpak': ('flatpaks', FLATPAK_ID), 'service': ('services', SERVICE_NAME),
               'mobile': ('mobile_admins', IDENTITY)}
    if kind not in choices:
        raise ValueError('Only flatpak, user service and Tailscale identity grants are supported.')
    key, pattern = choices[kind]
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError('Invalid allowlist entry.')
    if value == 'open-ci-hub-host-agent.service':
        raise ValueError('Host agent cannot administer itself.')
    if confirm is not True:
        raise PermissionError('Explicit --confirm is required from the host user.')
    data = policy_data()
    if value not in data[key]:
        data[key].append(value)
        secure_write(POLICY, json.dumps(data, indent=2) + '\n')
    return data

def _check_action(kind, target):
    if kind not in KINDS:
        raise ValueError('Host action not supported.')
    if not isinstance(target, str):
        raise ValueError('Host action target is invalid.')
    matcher = FLATPAK_ID if kind == 'flatpak_install' else SERVICE_NAME
    if not matcher.fullmatch(target) or target == 'open-ci-hub-host-agent.service':
        raise ValueError('Host action target rejected.')
    policy = policy_data()
    key = 'flatpaks' if kind == 'flatpak_install' else 'services'
    if target not in policy[key]:
        raise PermissionError('Target is not individually approved on this host.')
    return True

def _system():
    disk = shutil.disk_usage(Path.home())
    return {
        'hostname': platform.node()[:100],
        'system': platform.system(),
        'release': platform.release()[:100],
        'user': os.getuid() if hasattr(os, 'getuid') else None,
        'disk_free_gb': round(disk.free / (1024 ** 3), 2),
    }

def _exec(kind: str, target: str):
    _check_action(kind, target)
    if kind == 'flatpak_install':
        binary = '/usr/bin/flatpak'
        argv = [binary, 'install', '--user', '--noninteractive', '--assumeyes',
                '--or-update', 'flathub', target]
    else:
        binary = '/usr/bin/systemctl'
        argv = [binary, '--user', '--no-ask-password', kind.split('_', 1)[1], target]
    if not Path(binary).is_file():
        raise RuntimeError('Required host tool is unavailable: ' + Path(binary).name)
    # No shell, terminal, arbitrary environment, root privilege or command templates.
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(Path.home()), 'LANG': 'C.UTF-8'}
    for key in ('XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS', 'XDG_DATA_HOME'):
        if os.environ.get(key):
            env[key] = os.environ[key]
    try:
        result = subprocess.run(argv, shell=False, env=env, cwd='/',
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=EXEC_TIMEOUT, text=True)
    except subprocess.TimeoutExpired:
        raise RuntimeError('Host operation timed out; inspect its actual state before retrying.') from None
    except OSError:
        raise RuntimeError('Host operation could not start.') from None
    if result.returncode:
        # The output may disclose local information or tokens; do not return it to mobile clients.
        raise RuntimeError('Host tool rejected the operation. Check Flatpak remote, systemd user access and logs.')
    return {'ok': True, 'message': 'Host operation completed successfully.',
            'kind': kind, 'target': target}

def _append_audit(actor, kind, target, outcome):
    if STATE.is_symlink() or AUDIT.is_symlink():
        raise RuntimeError('Unsafe audit log path.')
    record = json.dumps({'ts': int(time.time()), 'actor': actor, 'kind': kind,
                         'target': target, 'outcome': outcome}, separators=(',', ':')) + '\n'
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(AUDIT, flags, 0o600)
    try:
        with os.fdopen(fd, 'a', encoding='utf-8') as stream:
            stream.write(record)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        pass

class HostController:
    def __init__(self):
        self.lock = threading.RLock()
        self.pending = {}

    def process(self, req):
        if not isinstance(req, dict):
            raise ValueError('Expected a JSON object.')
        op = req.get('op')
        if op == 'status':
            policy = policy_data()
            return {'available': True, 'system': _system(),
                    'allowed_flatpaks': policy['flatpaks'],
                    'allowed_services': policy['services'],
                    'note': 'Non-root host user. No arbitrary commands or package-manager privileges.'}
        if op not in ('preview', 'execute'):
            raise ValueError('Unsupported host agent operation.')
        actor = req.get('actor')
        kind, target = req.get('kind'), req.get('target')
        if not isinstance(actor, str) or not 1 <= len(actor) <= 200 or not (
                actor == 'local-owner' or IDENTITY.fullmatch(actor)):
            raise ValueError('Invalid actor identity.')
        _check_action(kind, target)
        if actor != 'local-owner' and actor not in policy_data()['mobile_admins']:
            raise PermissionError('This mobile identity has not been approved for host changes.')
        if op == 'preview':
            with self.lock:
                now = time.monotonic()
                self.pending = {x: item for x, item in self.pending.items() if item[0] > now}
                if len(self.pending) >= MAX_PENDING:
                    raise RuntimeError('Too many pending host approvals.')
                token = secrets.token_urlsafe(30)
                self.pending[token] = (now + PREVIEW_TTL, actor, kind, target)
            return {'preview_token': token, 'expires_in': PREVIEW_TTL,
                    'description': kind.replace('_', ' ') + ': ' + target,
                    'warning': 'Confirm to run the allowlisted action as your desktop user.'}
        token = req.get('preview_token')
        if not isinstance(token, str) or len(token) > 120:
            raise ValueError('Missing/invalid approval token.')
        with self.lock:
            item = self.pending.pop(token, None)
            if not item or item[0] < time.monotonic() or item[1:] != (actor, kind, target):
                raise PermissionError('Preview approval expired, mismatched or already used.')
        try:
            result = _exec(kind, target)
        except (RuntimeError, PermissionError, ValueError):
            _append_audit(actor, kind, target, 'failed')
            raise
        _append_audit(actor, kind, target, 'success')
        return result

class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        if hasattr(socket, 'SO_PEERCRED'):
            credentials = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _pid, uid, _gid = struct.unpack('3i', credentials)
            if uid != os.getuid():
                return
        self.request.settimeout(5)
        try:
            raw = self.rfile.readline(MAX_BYTES + 1)
            if not raw.endswith(b'\n') or len(raw) > MAX_BYTES:
                raise ValueError('Invalid host-agent request framing or size.')
            request = json.loads(raw.decode('utf-8'))
            response = {'ok': True, 'data': self.server.controller.process(request)}
        except (ValueError, RuntimeError, PermissionError, OSError, UnicodeError) as exc:
            response = {'ok': False, 'error': str(exc)[:250]}
        self.wfile.write((json.dumps(response) + '\n').encode('utf-8'))

class _UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

def serve(path=SOCKET, controller=None):
    if os.name != 'posix' or not hasattr(socket, 'AF_UNIX') or os.geteuid() == 0:
        raise RuntimeError('Host agent must run as a non-root Linux desktop user.')
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise RuntimeError('Refusing symlinked local host state.')
    os.chmod(path.parent, 0o700)
    if path.exists() or path.is_symlink():
        entry = path.lstat()
        if not stat.S_ISSOCK(entry.st_mode) or entry.st_uid != os.getuid():
            raise RuntimeError('Unexpected host socket path. Refusing to replace it.')
        try:
            with socket.socket(socket.AF_UNIX) as probe:
                probe.settimeout(0.5)
                probe.connect(str(path))
                raise RuntimeError('Host agent is already running.')
        except (ConnectionRefusedError, FileNotFoundError):
            path.unlink()
    server = _UnixServer(str(path), _Handler)
    server.controller = controller or HostController()
    os.chmod(path, 0o600)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        if path.exists() and stat.S_ISSOCK(path.lstat().st_mode):
            path.unlink()

def client_request(request, socket_path=SOCKET):
    """Trusted same-UID dashboard transport; never send this socket into CI containers."""
    if not isinstance(request, dict):
        raise ValueError('Invalid host request.')
    wire = (json.dumps(request, separators=(',', ':')) + '\n').encode()
    if len(wire) > MAX_BYTES:
        raise ValueError('Host request too long.')
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(6)
            conn.connect(str(socket_path))
            conn.sendall(wire)
            with conn.makefile('rb') as stream:
                raw = stream.readline(10000)
    except (OSError, TimeoutError):
        raise RuntimeError('Host agent is not running. Enable the local user service first.') from None
    if not raw.endswith(b'\n'):
        raise RuntimeError('Host agent reply was invalid.')
    response = json.loads(raw)
    if not isinstance(response, dict) or response.get('ok') is not True:
        raise RuntimeError(str(response.get('error', 'Host agent refused request.'))[:250])
    return response['data']

def main(argv=None):
    ap = argparse.ArgumentParser(description='Local, unprivileged, allowlisted host management agent')
    cmds = ap.add_subparsers(dest='command', required=True)
    cmds.add_parser('serve')
    cmds.add_parser('status')
    allow_cmd = cmds.add_parser('allow')
    allow_cmd.add_argument('kind', choices=('flatpak','service','mobile'))
    allow_cmd.add_argument('value')
    allow_cmd.add_argument('--confirm', action='store_true')
    args = ap.parse_args(argv)
    try:
        if args.command == 'serve':
            serve()
            return 0
        elif args.command == 'allow':
            data = allow(args.kind, args.value, confirm=args.confirm)
        else:
            data = client_request({'op':'status'})
        print(json.dumps(data, indent=2))
        return 0
    except (RuntimeError, ValueError, PermissionError, OSError) as exc:
        print('Host agent:', exc, file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
