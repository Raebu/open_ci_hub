#!/usr/bin/env python3
"""Owner-local MCP stdio endpoint for OpenAI Secure MCP Tunnel.

This is a narrowly scoped, direct PC tool surface. Transport access is
controlled by the owner's tunnel-client process and the connected ChatGPT
workspace. Stdio MCP DOES NOT cryptographically authenticate the original
natural-language ChatGPT message. Never expose arbitrary shell/sudo here.
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
import tempfile
from urllib.request import urlopen

from hub import secure_write

ROOT = Path(__file__).resolve().parent
PERMISSIONS = ROOT / '.local' / 'direct-mcp' / 'permissions.json'
SCHEMA = 'open-ci-hub.direct-mcp-consent.v1'
DOC_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\.(?:md|txt)$')
MAX_CONTENT_BYTES = 4000
MAX_INPUT_BYTES = 16000
VERSION = '2025-06-18'

TOOLS = [
    {
        'name': 'get_pc_status',
        'description': ('Read live system resources and state of existing Garuda Open CI Hub '
                        'and Raeburn Local CI services. Does not run shell commands or change anything.'),
        'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    },
    {
        'name': 'inspect_ci_failure',
        'description': ('Read native GitHub Actions/GitLab CI job and redacted failure evidence '
                        'for a named run, including an actual log excerpt when available. '
                        'Never retry or modify workflows.'),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'provider': {'type': 'string', 'enum': ['github', 'gitlab']},
                'repository': {'type': 'string', 'minLength': 3, 'maxLength': 300},
                'run_id': {'type': 'string', 'pattern': r'^[0-9]{1,18}$'},
            },
            'required': ['provider', 'repository', 'run_id'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'create_pc_document',
        'description': ('Create one new Markdown/text document exclusively in '
                        '~/Documents/OpenCIHubTasks, maximum 4,000 UTF-8 bytes. '
                        'No overwrite, code execution, arbitrary file access or shell. '
                        'Requires explicit one-time local owner enrolment.'),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'filename': {'type': 'string', 'pattern': DOC_NAME.pattern},
                'content': {'type': 'string', 'minLength': 1, 'maxLength': MAX_CONTENT_BYTES},
            },
            'required': ['filename', 'content'],
            'additionalProperties': False,
        },
    },
]


def require_nonroot() -> None:
    if os.name != 'posix' or os.geteuid() == 0:
        raise PermissionError('The direct MCP server must run as the ordinary Garuda user, never root.')


def consent_status(path: Path = PERMISSIONS) -> bool:
    if not path.exists():
        return False
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
        raise PermissionError('Unsafe MCP consent file.')
    st = path.stat()
    if st.st_uid != os.geteuid() or (st.st_mode & 0o077):
        raise PermissionError('MCP consent must be private and owned by the host user.')
    data = json.loads(path.read_text('utf-8'))
    if not isinstance(data, dict) or data.get('schema') != SCHEMA or type(data.get('document_creation')) is not bool:
        raise PermissionError('Invalid MCP consent policy.')
    return data['document_creation']


def set_consent(enabled: bool, confirm: bool = False) -> dict:
    require_nonroot()
    if not confirm:
        raise PermissionError('One-time owner confirmation required locally.')
    if PERMISSIONS.exists() and PERMISSIONS.is_symlink():
        raise PermissionError('Symlinked policy file forbidden.')
    if PERMISSIONS.exists():
        consent_status(PERMISSIONS)  # Refuse to overwrite unsafe policy.
    secure_write(PERMISSIONS, json.dumps({'schema': SCHEMA, 'document_creation':enabled},
                                         sort_keys=True) + '\n')
    return {'document_creation':enabled, 'scope':'new .md/.txt files in ~/Documents/OpenCIHubTasks'}


def _http_health(port: int) -> dict:
    try:
        with urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as resp:
            value = json.load(resp)
        if isinstance(value, dict):
            result = {k:value[k] for k in ('ok','status','worker','active_or_queued','max_concurrent_builds')
                      if k in value and isinstance(value[k], (str,int,bool,type(None)))}
            return {'reachable':True, **result}
    except (OSError, TimeoutError, ValueError):
        pass
    return {'reachable':False}


def _systemd_state(unit: str) -> str:
    try:
        result = subprocess.run(['/usr/bin/systemctl','--user','is-active',unit],
                                stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=3, check=False)
        state = result.stdout.strip()
        return state if state in ('active','inactive','failed','activating','deactivating') else 'unverified'
    except (OSError, subprocess.TimeoutExpired):
        return 'unverified'


def pc_status() -> dict:
    require_nonroot()
    disk = shutil.disk_usage(Path.home())
    memory = {}
    try:
        for line in Path('/proc/meminfo').read_text('ascii').splitlines():
            name, _, amount = line.partition(':')
            if name in ('MemTotal','MemAvailable','SwapFree'):
                memory[name.lower() + '_mb'] = int(amount.strip().split()[0]) // 1024
    except (OSError, ValueError, IndexError):
        memory = {'status':'unverified'}
    return {
        'host_kind':'live_owner_garuda_linux',
        'disk_free_gb':round(disk.free/(1024**3), 2),
        'load_average_1m':round(os.getloadavg()[0],2),
        'memory':memory,
        'services': {
            'dashboard':_systemd_state('open-ci-hub-dashboard.service'),
            'host_agent':_systemd_state('open-ci-hub-host-agent.service'),
            'legacy_ci':_systemd_state('custom-ci-api.service'),
        },
        'dashboard_health':_http_health(8765),
        'legacy_ci_health':_http_health(8090),
        'document_creation_enabled':consent_status(),
        'provenance_note':'Live local data; MCP stdio alone does not sign the originating ChatGPT message.',
    }


def create_document(filename: str, content: str) -> dict:
    require_nonroot()
    if not consent_status():
        raise PermissionError('Document creation is disabled. The PC owner must enrol this capability locally.')
    if not isinstance(filename,str) or not DOC_NAME.fullmatch(filename):
        raise ValueError('Only a safe new .md or .txt basename is allowed.')
    if not isinstance(content,str) or not 1 <= len(content.encode('utf-8')) <= MAX_CONTENT_BYTES or '\x00' in content:
        raise ValueError('UTF-8 document must contain 1–4000 bytes, without NUL.')
    output_dir = Path.home() / 'Documents' / 'OpenCIHubTasks'
    if output_dir.parent.is_symlink() or output_dir.is_symlink():
        raise PermissionError('Refusing symlinked document directory.')
    output_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    stat = output_dir.stat()
    if stat.st_uid != os.geteuid() or (stat.st_mode & 0o077):
        raise PermissionError('Document destination must be private and owner-owned.')
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os,'O_NOFOLLOW',0)
    target = output_dir / filename
    payload = content.encode('utf-8')
    fd = os.open(target, flags, 0o600)
    try:
        with os.fdopen(fd,'wb') as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {'result':'created','filename':filename,
            'folder':'~/Documents/OpenCIHubTasks',
            'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}


def invoke_tool(name: str, arguments: dict) -> dict:
    if not isinstance(arguments,dict):
        raise ValueError('Tool arguments must be a JSON object.')
    if name == 'get_pc_status':
        if arguments:
            raise ValueError('This tool accepts no arguments.')
        return pc_status()
    if name == 'create_pc_document':
        if set(arguments) != {'filename','content'}:
            raise ValueError('Expected filename and content only.')
        return create_document(arguments['filename'],arguments['content'])
    if name == 'inspect_ci_failure':
        if set(arguments) != {'provider','repository','run_id'}:
            raise ValueError('Expected provider, repository and run_id only.')
        from operations import safe_repo, safe_run_id
        provider, repo = arguments['provider'],safe_repo(arguments['repository'])
        run = safe_run_id(arguments['run_id'])
        if provider not in ('github','gitlab'):
            raise ValueError('Provider must be github or gitlab.')
        try:
            from ci_failure_diagnostics import diagnose
        except ImportError:
            raise RuntimeError('Failure inspection requires Open CI Hub main including PR #35.') from None
        return diagnose(provider,repo,run,include_logs=True)
    raise ValueError('Unknown MCP tool.')


def mcp_response(message: dict) -> dict | None:
    method = message.get('method')
    rid = message.get('id')
    if not isinstance(method,str):
        raise ValueError('MCP method must be a string.')
    if 'id' not in message:  # MCP notifications have no response.
        return None
    if rid is None or type(rid) not in (str,int):
        raise ValueError('MCP request id must be a string or integer.')
    if method == 'initialize':
        return {'jsonrpc':'2.0', 'id':rid, 'result':{
            'protocolVersion':VERSION,
            'capabilities':{'tools':{'listChanged':False}},
            'serverInfo':{'name':'open-ci-hub-direct-pc','version':'0.1.0'},
        }}
    if method == 'ping':
        return {'jsonrpc':'2.0','id':rid,'result':{}}
    if method == 'tools/list':
        return {'jsonrpc':'2.0','id':rid,'result':{'tools':TOOLS}}
    if method == 'tools/call':
        params = message.get('params',{})
        if not isinstance(params,dict):
            raise ValueError('Invalid tools/call params.')
        try:
            result = invoke_tool(params.get('name'),params.get('arguments',{}))
            return {'jsonrpc':'2.0','id':rid,'result':{
                'content':[{'type':'text','text':json.dumps(result,sort_keys=True,ensure_ascii=False)}],
                'isError':False,
            }}
        except (ValueError,PermissionError,RuntimeError,OSError,TypeError) as e:
            # Only controlled exception classes; no raw subprocess logs or secrets.
            return {'jsonrpc':'2.0','id':rid,'result':{
                'content':[{'type':'text','text':str(e)[:300]}],
                'isError':True,
            }}
    return {'jsonrpc':'2.0','id':rid,'error':{'code':-32601,'message':'Method not found.'}}


def serve(in_stream=None, out_stream=None) -> None:
    require_nonroot()
    input_stream = in_stream if in_stream is not None else sys.stdin
    output_stream = out_stream if out_stream is not None else sys.stdout
    for line in input_stream:
        if len(line.encode('utf-8')) > MAX_INPUT_BYTES:
            continue  # Do not process oversized untrusted requests.
        try:
            request = json.loads(line)
            if not isinstance(request,dict) or request.get('jsonrpc') != '2.0':
                continue
            response = mcp_response(request)
            if response is not None:
                output_stream.write(json.dumps(response,ensure_ascii=False,separators=(',',':'))+'\n')
                output_stream.flush()
        except (ValueError,UnicodeError,TypeError):
            # Bad protocol records cannot execute actions.
            continue


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Owner-local, bounded ChatGPT Secure MCP Tunnel server')
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('serve','status','enable-documents','disable-documents'):
        item=sub.add_parser(command)
        if command in ('enable-documents','disable-documents'):
            item.add_argument('--confirm', action='store_true')
    args = parser.parse_args(argv)
    try:
        require_nonroot()
        if args.command == 'serve':
            serve()
        elif args.command == 'status':
            print(json.dumps(pc_status(), indent=2))
        else:
            print(json.dumps(set_consent(args.command=='enable-documents', args.confirm),indent=2))
        return 0
    except (RuntimeError, ValueError, PermissionError, OSError) as err:
        print('Open CI direct MCP error: '+str(err)[:250],file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
