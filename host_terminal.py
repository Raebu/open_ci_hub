"""Explicitly approved interactive PTY sessions on the desktop host.

This is a *new* shell session, not a screen scraper or mirroring an existing
Konsole window. It runs as the non-root host user and never in CI containers.
"""
from __future__ import annotations
import base64
import os
import pty
import re
import secrets
import selectors
import signal
import subprocess
import threading
import time

IDLE_SECONDS = 300
ABSOLUTE_SECONDS = 900
MAX_SESSIONS = 1
MAX_WRITE = 512
MAX_READ = 4096
SHELLS = ('/usr/bin/bash','/bin/bash','/usr/bin/fish')

class TerminalManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.sessions = {}

    def _stop(self, item):
        process=item['process']
        if process.poll() is None:
            try:
                os.killpg(process.pid,signal.SIGTERM)
            except (OSError,ProcessLookupError):
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid,signal.SIGKILL)
                except (OSError,ProcessLookupError):
                    pass
        try: os.close(item['master'])
        except OSError: pass

    def _expire(self):
        now=time.monotonic()
        for key,item in list(self.sessions.items()):
            if (now-item['last']>IDLE_SECONDS or now-item['created']>ABSOLUTE_SECONDS
                    or item['process'].poll() is not None):
                self._stop(item)
                del self.sessions[key]

    def open(self, actor, *, shell='/usr/bin/bash'):
        if os.name!='posix' or os.geteuid()==0:
            raise PermissionError('Owner terminal requires a non-root POSIX host agent.')
        if shell not in SHELLS or not os.path.isfile(shell):
            raise ValueError('Unsupported locally installed shell.')
        with self.lock:
            self._expire()
            if len(self.sessions)>=MAX_SESSIONS:
                raise RuntimeError('Another host terminal is in use. Close it before opening a new session.')
            master,slave=pty.openpty()
            env={'HOME':os.path.expanduser('~'), 'PATH':'/usr/local/bin:/usr/bin:/bin',
                 'TERM':'xterm-256color','LANG':'C.UTF-8','SHELL':shell}
            for key in ('XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS','XDG_SESSION_TYPE'):
                if os.environ.get(key):
                    env[key]=os.environ[key]
            try:
                p=subprocess.Popen([shell,'-i'],stdin=slave,stdout=slave,stderr=slave,
                                   env=env,cwd=os.path.expanduser('~'),
                                   start_new_session=True,close_fds=True)
            except Exception:
                os.close(master);os.close(slave)
                raise
            os.close(slave)
            os.set_blocking(master,False)
            sid=secrets.token_urlsafe(24)
            now=time.monotonic()
            self.sessions[sid]={'actor':actor,'process':p,'master':master,
                                'created':now,'last':now}
            return {'session_id':sid,'expires_in':ABSOLUTE_SECONDS,
                    'idle_timeout':IDLE_SECONDS,
                    'notice':'New unprivileged host PTY, not an existing Konsole window.'}

    def _session(self,actor,sid):
        if not isinstance(sid,str) or len(sid)>100:
            raise ValueError('Invalid PTY session identifier.')
        self._expire()
        item=self.sessions.get(sid)
        if not item or item['actor']!=actor:
            raise PermissionError('Host terminal session expired or not owned by actor.')
        item['last']=time.monotonic()
        return item

    def read(self,actor,sid):
        with self.lock:
            item=self._session(actor,sid)
            with selectors.DefaultSelector() as ready:
                ready.register(item['master'],selectors.EVENT_READ)
                buffer=bytearray()
                if ready.select(timeout=.025):
                    while len(buffer)<MAX_READ:
                        try: chunk=os.read(item['master'],min(1024,MAX_READ-len(buffer)))
                        except (BlockingIOError,OSError): break
                        if not chunk: break
                        buffer.extend(chunk)
            return {'session_id':sid,'data_b64':base64.b64encode(buffer).decode('ascii'),
                    'running':item['process'].poll() is None}

    def write(self,actor,sid,data):
        if not isinstance(data,str) or len(data)>1500:
            raise ValueError('Invalid PTY input.')
        try: payload=base64.b64decode(data,validate=True)
        except (ValueError,Exception):
            raise ValueError('Invalid base64 PTY input.') from None
        if not payload or len(payload)>MAX_WRITE:
            raise ValueError('Terminal write must be 1-512 bytes.')
        with self.lock:
            item=self._session(actor,sid)
            try: n=os.write(item['master'],payload)
            except (BlockingIOError,OSError):
                raise RuntimeError('Terminal cannot currently accept input.') from None
            return {'accepted':n,'session_id':sid}

    def close(self,actor,sid):
        with self.lock:
            item=self._session(actor,sid)
            self._stop(item)
            del self.sessions[sid]
            return {'closed':True}

    def shutdown(self):
        with self.lock:
            for item in self.sessions.values():
                self._stop(item)
            self.sessions.clear()
