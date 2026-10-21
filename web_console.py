"""Local-first, provider-neutral web console for Open CI Hub.

Security boundary: only listens on IPv4 loopback. Remote access is supported
exclusively through an operator-configured Tailscale Serve reverse proxy with
identity headers and an independently approved user allowlist. Never use Funnel.
"""
from __future__ import annotations
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import shutil
import subprocess
import sys
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
import webbrowser

from providers import ADAPTERS
from audit import AuditLog
from operations import list_repositories, list_pipelines, pipeline_action, system_health
from pipeline_details import list_jobs, job_log, list_artifacts

SESSION_TTL = 12 * 3600
LIMIT_BYTES = 4096
ALLOWED_LOGIN = re.compile(r'^[^\s<>@]{1,100}@[^\s<>@]{1,200}$')
REPO_NAME = re.compile(r'^[A-Za-z0-9_.-]{1,100}(?:/[A-Za-z0-9_.-]{1,100})?$')


def read_data(path: Path, default):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def create_repo(provider: str, name: str, visibility: str, description: str = '') -> dict[str, str]:
    """Create a remote repository without running shells or copying account tokens."""
    if provider not in ('github', 'gitlab'):
        raise ValueError('Only repository hosting providers can create repositories.')
    if not isinstance(name, str) or not REPO_NAME.fullmatch(name) or name.startswith(('.', '-')) or '/.' in name or '/-' in name or '..' in name:
        raise ValueError('Invalid repository name (use OWNER/REPO or REPO).')
    if visibility not in ('private', 'public'):
        raise ValueError('Choose private or public visibility.')
    if not isinstance(description, str) or len(description) > 250 or any(ord(c) < 32 for c in description):
        raise ValueError('Description is too long or contains control characters.')
    if provider == 'github':
        args = ['gh', 'repo', 'create', name, '--' + visibility, '--add-readme']
        if description:
            args += ['--description', description]
    else:
        args = ['glab', 'repo', 'create', name, '--' + visibility, '--skipGitInit']
        if description:
            args += ['--description', description]
    if not shutil.which(args[0]):
        raise RuntimeError(f'{args[0]} CLI is not installed on the PC.')
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=90, check=True, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise RuntimeError('Repository creation timed out. Check provider before retrying.') from None
    except subprocess.CalledProcessError:
        raise RuntimeError('Repository creation failed; verify sign-in, organisation permissions and name availability.') from None
    # Never return arbitrary CLI output into HTML. Reconstruct canonical URL.
    if '/' not in name:
        # A CLI may create it in the signed-in personal namespace, which is unknown here.
        return {'message': 'Repository creation requested. Check your account for its URL.', 'url': ''}
    if provider == 'github':
        return {'message': 'Repository created.', 'url': 'https://github.com/' + name}
    return {'message': 'Repository created. View it on your signed-in GitLab instance.', 'url': ''}


def launch_signin(provider: str, setup: bool = False, group: bool = False, add: bool = False) -> str:
    """Open interactive official CLI sign-in in a *visible local terminal*.

    The CLI remains the OAuth client. No tokens are returned to the web server.
    """
    commands = {
        'github': ['gh', 'auth', 'login', '--hostname', 'github.com', '--web', '--scopes', 'repo'],
        'gitlab': ['glab', 'auth', 'login', '--hostname', 'gitlab.com', '--web'],
        'circleci': ['circleci', 'auth', 'login'],
    }
    if provider not in commands:
        raise ValueError('Unsupported provider')
    # Open sign-in/setup in a separate interactive wrapper that holds its terminal
    # open after failure or success, so users can inspect the final result.
    args = [sys.executable, str(Path(__file__).resolve().parent / 'terminal_session.py'),
            provider, ('add-group' if group else 'add-project') if add else ('group' if group else ('setup' if setup else 'login'))]
    if os.name == 'nt':
        # Use a visible PowerShell window for CLIs that require interactive prompts.
        # All arguments are static and never come from incoming HTTP requests.
        import subprocess as sp
        quoted = sp.list2cmdline(args)
        sp.Popen(['cmd', '/c', 'start', 'Open CI Hub Sign-in', 'cmd', '/k', quoted],
                 stdout=sp.DEVNULL, stderr=sp.DEVNULL, creationflags=getattr(sp, 'CREATE_NEW_PROCESS_GROUP', 0))
    elif sys.platform == 'darwin':
        import shlex
        script = shlex.join(args)   # fully quoted local path and static arguments
        subprocess.Popen(['osascript', '-e', 'tell application "Terminal" to activate',
                          '-e', f'tell application "Terminal" to do script {json.dumps(script)}'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        terminals = [
            ('x-terminal-emulator', ['x-terminal-emulator', '-e', *args]),
            ('gnome-terminal', ['gnome-terminal', '--', *args]),
            ('konsole', ['konsole', '-e', *args]),
            ('xfce4-terminal', ['xfce4-terminal', '--execute', *args]),
            ('xterm', ['xterm', '-e', *args]),
        ]
        for binary, command in terminals:
            if shutil.which(binary):
                subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
                break
        else:
            raise RuntimeError('No desktop terminal was found. Run the provider sign-in from hub.py menu.')
    label = 'additional runner setup' if add else ('group runner setup' if group else ('runner setup' if setup else 'sign-in'))
    return f'{provider.title()} {label} opened in a terminal on your PC. Complete the guided authorisation there.'


class WebConsole:
    def __init__(self, root: Path, port: int = 8765):
        self.root = root
        self.local_state = root / '.local'
        self.port = port
        self.lock = threading.RLock()
        self.sessions: dict[str, float] = {}
        self.server = None
        self.auth_file = self.local_state / 'dashboard-secret'
        if not self.auth_file.exists():
            from hub import secure_write
            secure_write(self.auth_file, secrets.token_urlsafe(48) + '\n')
        self.admin_secret = self.auth_file.read_text(encoding='utf-8').strip()
        self.users_file = self.local_state / 'mobile-users.json'
        self.pending_file = self.local_state / 'mobile-pending.json'
        self.static = root / 'dashboard'
        self.audit = AuditLog(self.local_state / 'activity.db')
        from app_runtime import Broker
        self.apps = Broker(self.local_state)

    def approved(self) -> dict[str, str]:
        """The account ID is the verified Tailscale login, not an untrusted client claim."""
        return read_data(self.users_file, {})

    def pending(self) -> list[str]:
        return read_data(self.pending_file, [])

    def remote_identity(self, handler: BaseHTTPRequestHandler) -> str | None:
        """Trust identity only for TLS/ts.net requests proxied through local Serve.

        WARNING: localhost software itself is trusted; never expose the backend to
        untrusted host processes, and never bind the backend to 0.0.0.0.
        """
        host = handler.headers.get('Host', '').split(':')[0].lower()
        user = handler.headers.get('Tailscale-User-Login', '').strip().lower()
        proto = handler.headers.get('X-Forwarded-Proto', '').strip().lower()
        if not (host.endswith('.ts.net') and proto == 'https' and handler.client_address[0] in ('127.0.0.1', '::1')):
            return None
        return user if ALLOWED_LOGIN.fullmatch(user) else None

    def local(self, handler: BaseHTTPRequestHandler) -> bool:
        host = handler.headers.get('Host', '')
        return host in (f'127.0.0.1:{self.port}', f'localhost:{self.port}')

    def role(self, handler: BaseHTTPRequestHandler) -> str:
        if self.local(handler):
            return 'owner' if self.local_session(handler) else 'none'
        user = self.remote_identity(handler)
        return self.approved().get(user, 'none') if user else 'none'

    def local_session(self, handler: BaseHTTPRequestHandler) -> bool:
        if self.local(handler):
            cookie = SimpleCookie()
            try:
                cookie.load(handler.headers.get('Cookie', ''))
                session = cookie['hub_session'].value
            except (KeyError, ValueError):
                return False
            expiry = self.sessions.get(session, 0)
            return bool(expiry > time.time())
        return False

    def authenticated(self, handler: BaseHTTPRequestHandler) -> bool:
        return self.role(handler) in ('owner', 'editor', 'viewer')

    def origin_valid(self, handler: BaseHTTPRequestHandler) -> bool:
        origin = handler.headers.get('Origin', '')
        host = handler.headers.get('Host', '')
        if self.local(handler):
            return origin == f'http://{host}'
        return self.remote_identity(handler) is not None and origin == f'https://{host}'

    def request_pair(self, user: str):
        with self.lock:
            pending = self.pending()
            if user not in pending and user not in self.approved():
                pending.append(user)
                from hub import secure_write
                secure_write(self.pending_file, json.dumps(pending) + '\n')

    def approve(self, user: str, approved: bool, role: str = 'viewer'):
        if not ALLOWED_LOGIN.fullmatch(user):
            raise ValueError('Invalid identity')
        if role not in ('viewer', 'editor'):
            raise ValueError('Invalid permission level')
        with self.lock:
            users, pending = self.approved(), self.pending()
            if approved:
                if user not in pending:
                    raise ValueError('No matching identity awaiting approval.')
                users[user] = role
            else:
                users.pop(user, None)
            pending = [u for u in pending if u != user]
            from hub import secure_write
            secure_write(self.users_file, json.dumps(users) + '\n')
            secure_write(self.pending_file, json.dumps(pending) + '\n')

    def serve(self, open_browser: bool = True):
        console = self
        class Handler(BaseHTTPRequestHandler):
            server_version = 'OpenCIHub/4'

            def setup(self):
                super().setup()
                self.connection.settimeout(15)

            def log_message(self, format, *args):
                # No request body, cookies or authentication credentials in logs.
                return

            def headers_common(self):
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('X-Frame-Options', 'DENY')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")

            def output(self, status: int, value, content_type: str = 'application/json', extra: list[tuple[str,str]] | None = None):
                payload = (json.dumps(value).encode('utf-8') if content_type == 'application/json' else value)
                self.send_response(status)
                self.headers_common()
                self.send_header('Content-Type', content_type + '; charset=utf-8')
                if extra:
                    for name, val in extra:
                        self.send_header(name, val)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def json_input(self):
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('JSON content type required.')
                try:
                    size = int(self.headers.get('Content-Length', '-1'))
                except ValueError:
                    size = -1
                if size <= 0 or size > LIMIT_BYTES:
                    raise ValueError('Invalid request size.')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('Expected a JSON object.')
                return data

            def app_api_trusted(self):
                # Requests must originate on loopback or through authenticated
                # private Tailscale Serve. Do NOT use Tailscale Funnel.
                if console.local(self):
                    return self.client_address[0] in ('127.0.0.1', '::1')
                user=console.remote_identity(self)
                return bool(user and console.approved().get(user) in ('owner','editor'))

            def app_api_actor(self):
                if not self.app_api_trusted():
                    raise PermissionError('Private application API transport unavailable.')
                header=self.headers.get('Authorization','')
                if not header.startswith('Bearer ') or header.count(' ')!=1:
                    raise PermissionError('Bearer application token required.')
                return console.apps.auth(header[len('Bearer '):])

            def app_api(self,path,method,data=None):
                if not self.app_api_trusted():
                    return self.output(403, {'error':'Private Tailscale or local API connection required.'})
                try:
                    who=self.app_api_actor()
                except (PermissionError,ValueError):
                    return self.output(401,{'error':'Missing, invalid or revoked application token.'})
                try:
                    if method=='GET':
                        if path=='/api/v1/apps/self':
                            return self.output(200,console.apps.overview(who))
                        if path.startswith('/api/v1/jobs/'):
                            return self.output(200,console.apps.inspect(who,path[len('/api/v1/jobs/'):]))
                    elif method=='POST':
                        if path=='/api/v1/jobs':
                            result=console.apps.submit(who,data.get('task'),data.get('payload'),
                                                       data.get('idempotency_key'))
                            return self.output(200 if result['duplicate'] else 201,result)
                        if path=='/api/v1/workers/claim':
                            return self.output(200,console.apps.claim(who))
                        if path=='/api/v1/workers/complete':
                            return self.output(200,console.apps.complete(
                                who,data.get('job_id'),data.get('lease_token'),
                                data.get('success'),data.get('result')))
                        if path=='/api/v1/workers/heartbeat':
                            return self.output(200,console.apps.heartbeat(
                                who,data.get('job_id'),data.get('lease_token')))
                    return self.output(404, {'error':'Unknown app API route.'})
                except PermissionError:
                    return self.output(403, {'error':'Not authorised for this application, task or job.'})
                except RuntimeError as exc:
                    return self.output(409, {'error':str(exc)[:250]})
                except (ValueError,TypeError,sqlite3.Error) as exc:
                    return self.output(400, {'error':str(exc)[:250]})

            def do_GET(self):
                parsed = urlsplit(self.path)
                path = parsed.path
                if len(self.path) > 2048:
                    return self.output(414, {'error': 'URL too long.'})
                if path.startswith('/api/v1/'):
                    if parsed.query:
                        return self.output(400, {'error':'Application API does not accept query strings.'})
                    return self.app_api(path,'GET')
                if path == '/health':
                    return self.output(200, {'ok': True})
                static = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/whatsapp-personal.js': ('whatsapp-personal.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
                if path in static:
                    filename, kind = static[path]
                    return self.output(200, (console.static / filename).read_bytes(), kind)
                if path in ('/api/repos', '/api/pipelines', '/api/health', '/api/activity', '/api/jobs', '/api/job-log', '/api/artifacts', '/api/ci/workflow', '/api/ci/failure', '/api/host', '/api/execution-evidence', '/api/machines', '/api/machines/probe', '/api/apps/inventory', '/api/apps/queue', '/api/deployments', '/api/ci/network'):
                    if not console.authenticated(self):
                        return self.output(401, {'error': 'Authentication required.'})
                    params = parse_qs(parsed.query, keep_blank_values=True)
                    if any(len(v) != 1 for v in params.values()):
                        return self.output(400, {'error': 'Duplicate query parameters are not allowed.'})
                    def param(key): return params.get(key, [''])[0]
                    try:
                        if path in ('/api/apps/inventory','/api/apps/queue'):
                            if console.role(self) not in ('owner','editor'):
                                return self.output(403, {'error':'Application management access required.'})
                            return self.output(200,console.apps.inventory() if path.endswith('inventory')
                                               else console.apps.queue_preview())
                        if path == '/api/ci/network':
                            if console.role(self) not in ('owner','editor'):
                                return self.output(403, {'error':'CI internet policy needs management permission.'})
                            from ci_network import network_overview
                            return self.output(200, network_overview())
                        if path == '/api/deployments':
                            if console.role(self) not in ('owner','editor'):
                                return self.output(403, {'error':'Deployment history access requires owner/editor.'})
                            from deployment_dashboard import inventory
                            return self.output(200, inventory())
                        if path == '/api/host':
                            from host_agent import client_request
                            try:
                                return self.output(200, client_request({'op':'status'}))
                            except RuntimeError as exc:
                                return self.output(200, {'available': False, 'message': str(exc)[:200]})
                        if path == '/api/repos':
                            return self.output(200, {'items': list_repositories(param('provider'), param('owner'))})
                        if path == '/api/pipelines':
                            return self.output(200, {'items': list_pipelines(param('provider'), param('repo'))})
                        if path == '/api/ci/workflow':
                            if param('provider') != 'github':
                                return self.output(400, {'error':'GitHub workflow policy is supported in this check; GitLab job failures are shown separately.'})
                            from operations import safe_repo
                            from ci_workflow_diagnostics import inspect_github
                            from fleet import _json_object
                            repo = safe_repo(param('repo'))
                            if len(repo.split('/')) != 2:
                                raise ValueError('Specify exact GitHub OWNER/REPO.')
                            meta = _json_object(['gh', 'api', f'repos/{repo}'])
                            branch = meta.get('default_branch')
                            if not isinstance(branch, str):
                                raise RuntimeError('Cannot establish repository default branch; workflow is unverified.')
                            return self.output(200, inspect_github(repo, branch))
                        if path == '/api/ci/failure':
                            from ci_failure_diagnostics import diagnose
                            logs = param('logs')
                            if logs not in ('', '0', '1'):
                                raise ValueError('logs must be 0 or 1.')
                            return self.output(200, diagnose(param('provider'), param('repo'),
                                                             param('run'), include_logs=logs=='1'))
                        if path == '/api/machines':
                            from machines import inventory
                            return self.output(200, inventory())
                        if path == '/api/machines/probe':
                            from machines import read, probe, hostname
                            host=hostname(param('host'))
                            if host not in [x['host'] for x in read()]:
                                return self.output(403, {'error':'Machine not enrolled.'})
                            return self.output(200, probe(host))
                        if path == '/api/execution-evidence':
                            from execution_proof import inspect
                            return self.output(200, inspect(param('provider'), param('repo'), param('run')))
                        if path == '/api/jobs':
                            return self.output(200, {'items': list_jobs(param('provider'), param('repo'), param('run'))})
                        if path == '/api/job-log':
                            return self.output(200, job_log(param('provider'), param('repo'), param('run'), param('job')))
                        if path == '/api/artifacts':
                            return self.output(200, {'items': list_artifacts(param('provider'), param('repo'), param('run'))})
                        if path == '/api/health':
                            return self.output(200, system_health())
                        if path == '/api/activity':
                            if console.role(self) != 'owner':
                                return self.output(403, {'error':'Only the local owner can inspect the audit trail.'})
                            return self.output(200, {'items': console.audit.recent()})
                    except (RuntimeError,ValueError,PermissionError,OSError) as exc:
                        return self.output(400, {'error': str(exc)[:300]})
                if path == '/api/overview':
                    if not console.authenticated(self):
                        return self.output(401, {'authenticated': False, 'mobile': bool(console.remote_identity(self)),
                                                 'identity': console.remote_identity(self)})
                    from hub import runner_state, metadata_path, docker_ready
                    ready = docker_ready()
                    providers = []
                    for name, adapter in ADAPTERS.items():
                        try:
                            signed_in = bool(adapter.authenticated())
                        except (RuntimeError, OSError):
                            signed_in = False
                        providers.append({'id': name, 'authenticated': signed_in,
                                          'configured': metadata_path(name).exists(),
                                          'runner': runner_state(name) if ready else 'Docker unavailable',
                                          'can_create_repo': name in ('github', 'gitlab')})
                    mobile = console.remote_identity(self)
                    return self.output(200, {'authenticated': True, 'providers': providers,
                                             'mode': 'mobile' if mobile else 'desktop',
                                             'identity': mobile,
                                             'role': console.role(self),
                                             'approved_users': console.approved() if not mobile else {},
                                             'pending_users': console.pending() if not mobile else []})
                self.output(404, {'error': 'Not found'})

            def do_POST(self):
                parsed = urlsplit(self.path)
                path = parsed.path
                if len(self.path)>2048:
                    return self.output(414, {'error':'URL too long.'})
                if path.startswith('/api/v1/'):
                    if parsed.query:
                        return self.output(400, {'error':'Application API does not accept query strings.'})
                    try:
                        data=self.json_input()
                    except (ValueError,TypeError,json.JSONDecodeError) as exc:
                        return self.output(400,{'error':str(exc)[:200]})
                    return self.app_api(path,'POST',data)
                if not console.origin_valid(self) or self.headers.get('X-Open-CI-Request') != '1':
                    return self.output(403, {'error': 'Origin or CSRF check failed.'})
                try:
                    data = self.json_input()
                    if path == '/api/session':
                        if not console.local(self) or not hmac.compare_digest(str(data.get('secret', '')), console.admin_secret):
                            return self.output(403, {'error': 'Invalid local session credentials.'})
                        session = secrets.token_urlsafe(36)
                        with console.lock:
                            console.sessions[session] = time.time() + SESSION_TTL
                        return self.output(200, {'ok': True}, extra=[('Set-Cookie',
                                   f'hub_session={session}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_TTL}')])
                    if path == '/api/mobile/request':
                        identity = console.remote_identity(self)
                        if not identity:
                            return self.output(403, {'error': 'Mobile sign-in requires Tailscale Serve.'})
                        console.request_pair(identity)
                        return self.output(202, {'message': 'Approval requested. Open the desktop dashboard to approve this identity.'})
                    if not console.authenticated(self):
                        return self.output(401, {'error': 'Sign in or request mobile approval first.'})
                    if path in ('/api/apps/control','/api/apps/priority'):
                        if console.role(self) not in ('owner','editor'):
                            return self.output(403, {'error':'Approved management identity required.'})
                        if data.get('confirm') is not True:
                            return self.output(400, {'error':'Explicit operation confirmation required.'})
                        actor=console.remote_identity(self) or 'local-owner'
                        if path.endswith('priority'):
                            result=console.apps.reprioritize(data.get('job_id'),
                                                              data.get('priority'),confirm=True)
                            console.audit.add(actor,'apps.priority',str(data.get('job_id'))[:32],'success')
                            return self.output(200,result)
                        action=data.get('action')
                        if action=='pause':
                            result=console.apps.control(paused=True,confirm=True)
                        elif action=='resume':
                            result=console.apps.control(paused=False,confirm=True)
                        elif action in ('quiet','balanced'):
                            result=console.apps.control(mode=action,confirm=True)
                        elif action=='performance':
                            if not console.local(self) or console.role(self)!='owner':
                                return self.output(403, {'error':'Performance mode requires local owner approval.'})
                            result=console.apps.control(mode=action,confirm=True)
                        elif action=='emergency_stop':
                            result=console.apps.emergency_stop(confirm=True)
                        else:
                            return self.output(400,{'error':'Unsupported application control.'})
                        console.audit.add(actor,'apps.'+action,'app-runtime','requested')
                        return self.output(200,result)
                    if path.startswith('/api/host/terminal/'):
                        from host_agent import client_request
                        operation=path.rsplit('/',1)[-1]
                        if operation not in ('preview','open','read','write','close'):
                            return self.output(404, {'error':'Unsupported terminal operation.'})
                        if console.role(self) not in ('owner','editor'):
                            return self.output(403, {'error':'Owner-approved terminal access required.'})
                        actor = 'local-owner' if console.role(self)=='owner' else console.remote_identity(self)
                        if not actor:
                            return self.output(403, {'error':'Verified host identity required.'})
                        if operation=='open' and data.get('confirm') is not True:
                            return self.output(400, {'error':'Explicit terminal confirmation required.'})
                        packet={'op':'terminal_'+operation,'actor':actor}
                        if operation=='open':
                            packet['preview_token']=data.get('preview_token')
                        if operation in ('read','write','close'):
                            packet['session_id']=data.get('session_id')
                        if operation=='write':
                            packet['data_b64']=data.get('data_b64')
                        answer=client_request(packet)
                        if operation in ('open','close'):
                            console.audit.add(actor,'host.terminal_'+operation,'user-shell','requested')
                        return self.output(200, answer)
                    if path == '/api/host/job':
                        from host_agent import client_request
                        role = console.role(self)
                        if role not in ('owner', 'editor'):
                            return self.output(403, {'error':'Host job details require management permission.'})
                        actor = 'local-owner' if role == 'owner' else console.remote_identity(self)
                        if not actor:
                            return self.output(403, {'error':'Host identity unavailable.'})
                        return self.output(200, client_request({
                            'op':'job', 'actor': actor, 'job_id':data.get('job_id')}))
                    if path in ('/api/host/preview', '/api/host/execute'):
                        from host_agent import client_request
                        role = console.role(self)
                        if role not in ('owner', 'editor'):
                            return self.output(403, {'error':'Host actions require an approved management identity.'})
                        if path.endswith('execute') and data.get('confirm') is not True:
                            return self.output(400, {'error':'Explicit host operation confirmation required.'})
                        actor = 'local-owner' if role == 'owner' else console.remote_identity(self)
                        if not actor:
                            return self.output(403, {'error':'Verified host management identity required.'})
                        action = {'op': 'execute' if path.endswith('execute') else 'preview',
                                  'actor': actor, 'kind': data.get('kind'), 'target': data.get('target')}
                        if action['op'] == 'execute':
                            action['preview_token'] = data.get('preview_token')
                        try:
                            result = client_request(action)
                        except (RuntimeError, ValueError, PermissionError) as exc:
                            if action['op'] == 'execute':
                                console.audit.add(actor, 'host.' + str(action['kind'])[:32],
                                                  str(action['target'])[:100], 'failed')
                            raise
                        if action['op'] == 'execute':
                            console.audit.add(actor, 'host.' + action['kind'], action['target'], 'success')
                        return self.output(200, result)
                    if path in ('/api/machines/add','/api/machines/remove'):
                        if not console.local(self) or console.role(self) != 'owner':
                            return self.output(403, {'error':'New machine trust must be approved from the owner desktop.'})
                        if data.get('confirm') is not True:
                            return self.output(400, {'error':'Explicit machine trust confirmation required.'})
                        from machines import add, remove
                        result=(add(data.get('name'),data.get('host'),confirm=True)
                                if path.endswith('add')
                                else remove(data.get('host'),confirm=True))
                        console.audit.add('local-owner',path.rsplit('/',1)[-1],str(data.get('host'))[:100],'success')
                        return self.output(200,result)
                    if path == '/api/pipeline':
                        if console.role(self) not in ('owner', 'editor'):
                            return self.output(403, {'error': 'Management permission required.'})
                        if data.get('confirm') is not True:
                            return self.output(400, {'error': 'Explicit confirmation required.'})
                        provider, repo, run_id, action = (data.get(k) for k in
                                                          ('provider','repo','id','action'))
                        # Validate before recording or calling external providers.
                        from operations import safe_repo, safe_run_id
                        if provider not in ('github','gitlab','circleci') or action not in ('retry','cancel'):
                            raise ValueError('Unsupported provider or operation.')
                        safe_repo(repo)
                        if provider == 'circleci':
                            from providers.circleci_cockpit import workflow_id
                            workflow_id(run_id)
                        else:
                            safe_run_id(run_id)
                        actor = console.remote_identity(self) or 'local-owner'
                        try:
                            pipeline_action(provider, repo, run_id, action)
                        except (RuntimeError,OSError) as exc:
                            console.audit.add(actor, 'pipeline.' + action, provider + ':' + repo + '#' + str(run_id), 'failed')
                            raise
                        console.audit.add(actor, 'pipeline.' + action, provider + ':' + repo + '#' + str(run_id), 'requested')
                        return self.output(200, {'message': 'Provider accepted the ' + action + ' request.'})
                    if path in ('/api/onboarding/plan', '/api/onboarding/apply', '/api/onboarding/check'):
                        from onboarding import make_plan, create_review_request
                        if console.role(self) not in ('owner', 'editor'):
                            return self.output(403, {'error': 'Management permission required.'})
                        plan = make_plan(data.get('provider'), data.get('repo'),
                                         branch=data.get('branch', 'main'),
                                         resource_class=data.get('resource_class', ''))
                        if path.endswith('check'):
                            from readiness import check
                            return self.output(200, check(plan.provider, plan.repo, plan.branch, data.get('resource_class', '')))
                        if path.endswith('plan'):
                            return self.output(200, plan.as_dict())
                        if data.get('confirm') is not True:
                            return self.output(400, {'error': 'Explicit approval required to modify a provider repository.'})
                        result = create_review_request(plan, approved=True)
                        console.audit.add(console.remote_identity(self) or 'local-owner',
                                          'onboarding.review', plan.provider + ':' + plan.repo,
                                          'requested')
                        return self.output(201, result)
                    if path == '/api/runner/add-gitlab':
                        if not console.local(self) or console.role(self) != 'owner':
                            return self.output(403, {'error': 'Runner setup must begin on the owner PC.'})
                        scope = data.get('scope')
                        if scope not in ('project', 'group'):
                            raise ValueError('Choose a GitLab project or group scope.')
                        return self.output(202, {'message': launch_signin('gitlab', setup=True,
                                                   group=scope == 'group', add=True)})
                    if path == '/api/runner/group-setup':
                        if not console.local(self) or console.role(self) != 'owner':
                            return self.output(403, {'error': 'Runner setup must begin on the owner PC.'})
                        return self.output(202, {'message': launch_signin('gitlab', setup=True, group=True)})
                    if path == '/api/runner/setup':
                        if not console.local(self) or console.role(self) != 'owner':
                            return self.output(403, {'error':'Runner setup must begin on the owner PC.'})
                        return self.output(202, {'message': launch_signin(str(data.get('provider','')), setup=True)})
                    if path == '/api/logout':
                        cookie = SimpleCookie()
                        try:
                            cookie.load(self.headers.get('Cookie', ''))
                            session = cookie['hub_session'].value
                        except (KeyError,ValueError):
                            session = ''
                        with console.lock:
                            console.sessions.pop(session, None)
                        return self.output(200, {'ok':True}, extra=[('Set-Cookie',
                            'hub_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')])
                    if path == '/api/provider/login':
                        if not console.local(self):
                            return self.output(403, {'error': 'Provider sign-in must begin on your PC.'})
                        return self.output(202, {'message': launch_signin(str(data.get('provider', '')))})
                    if path == '/api/repos':
                        if console.role(self) not in ('owner', 'editor'):
                            return self.output(403, {'error': 'Read-only mobile users cannot create repositories.'})
                        provider, name, visibility = (data.get(x, '') for x in ('provider', 'name', 'visibility'))
                        if visibility == 'public' and data.get('confirm_public') is not True:
                            return self.output(400, {'error': 'Explicit public repository confirmation is required.'})
                        result = create_repo(provider, name, visibility, data.get('description', ''))
                        console.audit.add(console.remote_identity(self) or 'local-owner', 'repo.create', provider + ':' + name, 'requested')
                        return self.output(201, result)
                    if path in ('/api/mobile/approve', '/api/mobile/revoke'):
                        if not console.local(self):
                            return self.output(403, {'error': 'Mobile access can only be approved from the PC.'})
                        user = str(data.get('user', '')).lower()
                        console.approve(user, path.endswith('approve'), str(data.get('role', 'viewer')))
                        console.audit.add('local-owner', 'access.approve' if path.endswith('approve') else 'access.revoke', user, 'success')
                        return self.output(200, {'ok': True})
                    if path == '/api/runner':
                        if console.role(self) not in ('owner', 'editor'):
                            return self.output(403, {'error': 'Read-only mobile users cannot control runners.'})
                        provider = data.get('provider')
                        action = data.get('action')
                        if provider not in ADAPTERS or action not in ('start', 'stop'):
                            raise ValueError('Unsupported provider/action.')
                        from hub import provider_action
                        provider_action(provider, action)
                        console.audit.add(console.remote_identity(self) or 'local-owner', 'runner.' + action, provider, 'requested')
                        return self.output(200, {'message': f'{provider.title()} runner {action} requested.'})
                    return self.output(404, {'error': 'Not found'})
                except (RuntimeError, ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
                    return self.output(400, {'error': str(exc)[:300]})

        server = ThreadingHTTPServer(('127.0.0.1', self.port), Handler)
        self.server = server
        if open_browser:
            url = f'http://127.0.0.1:{self.port}/#access={self.admin_secret}'
            print(f'Local dashboard: http://127.0.0.1:{self.port}/')
            print('A private admin session link has been opened in your browser. Never share its fragment.')
            webbrowser.open(url)
        try:
            server.serve_forever()
        finally:
            server.server_close()


def mobile_enable(port: int = 8765) -> None:
    if not shutil.which('tailscale'):
        raise RuntimeError('Install Tailscale on your PC and sign in before enabling mobile access.')
    try:
        subprocess.run(['tailscale', 'status', '--json'], capture_output=True, timeout=15, check=True)
        subprocess.run(['tailscale', 'serve', '--bg', str(port)], check=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise RuntimeError('Tailscale Serve could not be enabled. Check login, HTTPS and tailnet permissions.') from None
    print('Private mobile access enabled. Run `tailscale serve status` to see your HTTPS URL.')
    print('Do NOT use Tailscale Funnel. Open that URL on a phone signed into the same tailnet.')


def mobile_disable() -> None:
    if not shutil.which('tailscale'):
        raise RuntimeError('Tailscale is not installed.')
    subprocess.run(['tailscale', 'serve', '--https=443', 'off'], check=True, timeout=20)
    print('Disabled the HTTPS :443 Tailscale Serve mapping. Other mappings were not reset.')
