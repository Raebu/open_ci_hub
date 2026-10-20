#!/usr/bin/env python3
"""Private application job broker for Open CI Hub.

The API queues typed JSON work for separately deployed, allowlisted application
workers. It NEVER interprets payloads as commands or mounts CI Docker sockets.
"""
from __future__ import annotations
import argparse
from contextlib import closing
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import time

from hub import LOCAL
import parallel_scheduler as parallel

APP = re.compile(r'^[a-z][a-z0-9-]{1,48}$')
TASK = re.compile(r'^[a-z][a-z0-9_.-]{1,64}$')
KEY = re.compile(r'^oci_v1_[A-Za-z0-9_-]{40,90}$')
JOB = re.compile(r'^[a-f0-9]{32}$')
IDEMPOTENCY = re.compile(r'^[A-Za-z0-9._:-]{8,128}$')
ROLE = {'submit', 'worker'}
MAX_PAYLOAD = 2048
MAX_RESULT = 2048
MAX_PENDING = 100
MAX_TOTAL = 10000
MAX_ATTEMPTS = 3
LEASE_SECONDS = 300
MAX_LEASE_SECONDS = 1800
COMPLETE_RETENTION = 7 * 86400

class Broker:
    def __init__(self, state: Path | None = None):
        self.state = Path(state) if state is not None else LOCAL
        if self.state.is_symlink():
            raise RuntimeError('App state directory cannot be a symlink.')
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != 'nt':
            os.chmod(self.state, 0o700)
        self.path = self.state / 'application-runtime.sqlite'
        if self.path.is_symlink():
            raise RuntimeError('App database cannot be a symlink.')
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS apps (
                    app TEXT PRIMARY KEY, tasks TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS credentials (
                    token_hash TEXT PRIMARY KEY, app TEXT NOT NULL, role TEXT NOT NULL,
                    created REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(app) REFERENCES apps(app)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, app TEXT NOT NULL, task TEXT NOT NULL,
                    payload TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL,
                    lease_hash TEXT, lease_until REAL, result TEXT,
                    idempotency_key TEXT, FOREIGN KEY(app) REFERENCES apps(app),
                    UNIQUE(app, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_app_jobs
                    ON jobs(app, status, created);
            ''')
            parallel.init_schema(db)
        if os.name != 'nt':
            os.chmod(self.path, 0o600)

    def _db(self):
        conn = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA busy_timeout=5000')
        return closing(conn)

    @staticmethod
    def _check_app(app):
        if not isinstance(app,str) or not APP.fullmatch(app):
            raise ValueError('Invalid application ID.')
        return app

    @staticmethod
    def _check_tasks(tasks):
        if not isinstance(tasks,list) or not 1 <= len(tasks) <= 30 or any(
                not isinstance(t,str) or not TASK.fullmatch(t) for t in tasks):
            raise ValueError('Provide 1–30 validated task names.')
        if len(set(tasks))!=len(tasks):
            raise ValueError('Duplicate task names.')
        return tasks

    @staticmethod
    def _json_value(value, cap=MAX_PAYLOAD):
        if not isinstance(value,(dict,list)):
            raise ValueError('JSON object or array required.')
        try:
            wire=json.dumps(value,ensure_ascii=True,allow_nan=False,separators=(',',':'))
        except (TypeError,ValueError,OverflowError):
            raise ValueError('Invalid JSON work payload.') from None
        if len(wire.encode('utf-8'))>cap:
            raise ValueError('JSON work payload exceeds quota.')
        return wire

    def register(self,app,tasks,*,confirm=False):
        app=self._check_app(app)
        tasks=self._check_tasks(tasks)
        if confirm is not True:
            raise PermissionError('Only host owner can register an app with --confirm.')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM apps WHERE app=?',(app,)).fetchone():
                raise RuntimeError('Application is already registered; do not silently change permissions.')
            db.execute('INSERT INTO apps(app,tasks) VALUES(?,?)',(app,json.dumps(tasks)))
            db.commit()
        return {'app':app,'tasks':tasks,'enabled':True}

    def issue(self,app,role,*,confirm=False,node=None):
        app=self._check_app(app)
        if role not in ROLE:raise ValueError('Only submit/worker scopes exist.')
        if confirm is not True:
            raise PermissionError('Only host owner can issue API credentials with --confirm.')
        token='oci_v1_'+secrets.token_urlsafe(48)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT enabled FROM apps WHERE app=?',(app,)).fetchone()
            if row is None or not row['enabled']:
                raise PermissionError('Application not registered or disabled.')
            if role=='worker':
                if parallel.enabled(db):
                    if not isinstance(node,str) or not parallel.NODE_ID.fullmatch(node):
                        raise ValueError('Parallel workers require a locally approved --node.')
                    if not db.execute('SELECT 1 FROM compute_nodes WHERE id=? AND enabled=1',(node,)).fetchone():
                        raise PermissionError('Worker node not authorised.')
                elif node is not None:
                    raise PermissionError('Configure the worker node before binding its token.')
            elif node is not None:
                raise ValueError('Submitter keys cannot be bound to worker nodes.')
            db.execute('INSERT INTO credentials(token_hash,app,role,created,node_id) VALUES(?,?,?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(),app,role,time.time(),node))
            db.commit()
        return {'app':app,'scope':role,'node':node,'token':token,
                'notice':'Shown once. Never send this token to a browser, source code or CI log.'}

    def revoke(self,token_hash,*,confirm=False):
        if not isinstance(token_hash,str) or not re.fullmatch(r'[a-f0-9]{64}',token_hash):
            raise ValueError('Expected SHA-256 credential identifier.')
        if confirm is not True:raise PermissionError('Host owner confirmation required.')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            changed=db.execute('UPDATE credentials SET revoked=1 WHERE token_hash=?',
                               (token_hash,)).rowcount
            db.commit()
        return {'revoked':bool(changed)}

    def auth(self,token):
        if not isinstance(token,str) or not KEY.fullmatch(token):
            raise PermissionError('Invalid application credential.')
        digest=hashlib.sha256(token.encode()).hexdigest()
        with self._db() as db:
            row=db.execute('SELECT c.token_hash,c.app,c.role,c.node_id,a.tasks FROM credentials c JOIN apps a ON a.app=c.app '
                           'WHERE c.token_hash=? AND c.revoked=0 AND a.enabled=1',(digest,)).fetchone()
        if not row or not hmac.compare_digest(row['token_hash'],digest):
            raise PermissionError('Application credential was not recognised.')
        return {'app':row['app'],'role':row['role'],'tasks':json.loads(row['tasks']),'node':row['node_id']}

    @staticmethod
    def _require(who,role):
        if not isinstance(who,dict) or who.get('role')!=role:
            raise PermissionError('Application capability scope does not allow this operation.')

    def submit(self,who,task,payload,idempotency_key=None):
        self._require(who,'submit')
        if not isinstance(task,str) or not TASK.fullmatch(task) or task not in who['tasks']:
            raise PermissionError('Task is not authorised for this application.')
        wire=self._json_value(payload)
        if idempotency_key is not None and (
                not isinstance(idempotency_key,str) or not IDEMPOTENCY.fullmatch(idempotency_key)):
            raise ValueError('Invalid idempotency key.')
        app=who['app'];now=time.time();job=secrets.token_hex(16)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            if idempotency_key:
                existing=db.execute('SELECT id,task,payload,status FROM jobs WHERE app=? AND idempotency_key=?',
                                    (app,idempotency_key)).fetchone()
                if existing:
                    if existing['task']!=task or existing['payload']!=wire:
                        raise RuntimeError('Idempotency key was already used with different work.')
                    db.commit()
                    return {'job_id':existing['id'],'status':existing['status'],'duplicate':True}
            db.execute('DELETE FROM jobs WHERE status IN ("completed","failed") AND updated<?',
                       (now-COMPLETE_RETENTION,))
            pending=db.execute('SELECT COUNT(*) FROM jobs WHERE app=? AND status IN ("queued","leased")',
                               (app,)).fetchone()[0]
            total=db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
            if pending>=MAX_PENDING or total>=MAX_TOTAL:
                raise RuntimeError('Work queue quota reached; retry after processing existing jobs.')
            db.execute('INSERT INTO jobs(id,app,task,payload,status,attempts,created,updated,idempotency_key) '
                       'VALUES(?,?,?,?,?,?,?,?,?)',
                       (job,app,task,wire,'queued',0,now,now,idempotency_key))
            db.commit()
        return {'job_id':job,'status':'queued','duplicate':False}

    def inspect(self,who,job):
        if not isinstance(job,str) or not JOB.fullmatch(job):
            raise ValueError('Invalid job identifier.')
        with self._db() as db:
            r=db.execute('SELECT id,task,status,attempts,created,updated,result '
                         'FROM jobs WHERE id=? AND app=?',(job,who['app'])).fetchone()
        if not r:raise PermissionError('Job unavailable for this application.')
        return {'job_id':r['id'],'task':r['task'],'status':r['status'],'attempts':r['attempts'],
                'created':r['created'],'updated':r['updated'],
                'result':json.loads(r['result']) if r['result'] is not None else None}

    def claim(self,who,telemetry=None):
        self._require(who,'worker')
        now=time.time();app=who['app']
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE jobs SET status="failed",updated=?,lease_hash=NULL,lease_until=NULL,'
                       'result=?,reserved_node=NULL,reserved_memory_mb=0,reserved_vram_mb=0,'
                       'reserved_cpu_slots=0 WHERE app=? AND status="leased" AND lease_until<? AND attempts>=?',
                       (now,json.dumps({'error':'Retry limit exceeded after worker lease expiry.'}),
                        app,now,MAX_ATTEMPTS))
            db.execute('UPDATE jobs SET status="queued",updated=?,lease_hash=NULL,lease_until=NULL,'
                       'reserved_node=NULL,reserved_memory_mb=0,reserved_vram_mb=0,reserved_cpu_slots=0 '
                       'WHERE app=? AND status="leased" AND lease_until<? AND attempts<?',
                       (now,app,now,MAX_ATTEMPTS))
            reserved=None
            if parallel.enabled(db):
                row,reason,pool=parallel.select_job(db,app,who.get('node'),telemetry,now)
                if row:
                    reserved={'node':who['node'],'memory_mb':row['memory_mb'],
                              'vram_mb':row['vram_mb'],'cpu_slots':row['cpu_slots']}
            else:
                row=db.execute('SELECT id,task,payload,attempts FROM jobs WHERE app=? AND status="queued" '
                               'ORDER BY created,id LIMIT 1',(app,)).fetchone()
                reason='empty'
            if not row:
                db.commit()
                return {'job':None,'reason':reason}
            secret=secrets.token_urlsafe(36)
            db.execute('UPDATE jobs SET status="leased",attempts=attempts+1,lease_hash=?,'
                       'lease_until=?,updated=?,reserved_node=?,reserved_memory_mb=?,'
                       'reserved_vram_mb=?,reserved_cpu_slots=? WHERE id=?',
                       (hashlib.sha256(secret.encode()).hexdigest(),now+LEASE_SECONDS,now,
                        reserved['node'] if reserved else None,
                        reserved['memory_mb'] if reserved else 0,
                        reserved['vram_mb'] if reserved else 0,
                        reserved['cpu_slots'] if reserved else 0,row['id']))
            db.commit()
        return {'job':{'job_id':row['id'],'task':row['task'],'payload':json.loads(row['payload']),
                       'attempt':row['attempts']+1,'lease_token':secret,'lease_seconds':LEASE_SECONDS,
                       'reservation':reserved}}

    def _leased(self,db,who,job,lease_token):
        self._require(who,'worker')
        if not isinstance(job,str) or not JOB.fullmatch(job) or (
            not isinstance(lease_token,str) or len(lease_token)<40 or len(lease_token)>100):
            raise PermissionError('Invalid job lease.')
        row=db.execute('SELECT status,lease_until,lease_hash,attempts,reserved_node FROM jobs '
                       'WHERE app=? AND id=?',(who['app'],job)).fetchone()
        digest=hashlib.sha256(lease_token.encode()).hexdigest()
        if (not row or row['status']!='leased' or not isinstance(row['lease_until'],float)
                or row['lease_until']<=time.time() or not row['lease_hash']
                or not hmac.compare_digest(row['lease_hash'],digest) or
                row['reserved_node']!=who.get('node')):
            raise PermissionError('Lease expired, invalid or not owned by this worker.')
        return row

    def heartbeat(self,who,job,lease_token):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._leased(db,who,job,lease_token)
            until=time.time()+LEASE_SECONDS
            db.execute('UPDATE jobs SET lease_until=?,updated=? WHERE id=?',
                       (until,time.time(),job))
            db.commit()
        return {'job_id':job,'lease_seconds':LEASE_SECONDS}

    def complete(self,who,job,lease_token,success,result):
        if type(success) is not bool:raise ValueError('Explicit success boolean is required.')
        output=self._json_value(result,MAX_RESULT)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            state=self._leased(db,who,job,lease_token)
            status='completed' if success else ('queued' if state['attempts']<MAX_ATTEMPTS else 'failed')
            db.execute('UPDATE jobs SET status=?,result=?,lease_hash=NULL,lease_until=NULL,'
                       'reserved_node=NULL,reserved_memory_mb=0,reserved_vram_mb=0,'
                       'reserved_cpu_slots=0,updated=? WHERE id=?',
                       (status,output,time.time(),job))
            db.commit()
        return {'job_id':job,'status':status,'attempts':state['attempts']}

    def configure_node(self,node,memory_mb,vram_mb,cpu_slots,max_jobs,
                       *,reserve_memory_mb=None,reserve_vram_mb=None,confirm=False):
        if confirm is not True:raise PermissionError('Only the local owner can authorise a worker node.')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            value=parallel.register_node(db,node,memory_mb,vram_mb,cpu_slots,max_jobs,
                                         reserve_memory_mb,reserve_vram_mb)
            db.commit()
        return value

    def configure_task(self,app,task,memory_mb,vram_mb,cpu_slots,*,confirm=False):
        if confirm is not True:raise PermissionError('Only local owner can set task resource limits.')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            result=parallel.set_task(db,app,task,memory_mb,vram_mb,cpu_slots)
            db.commit()
        return result

    def configure_app_limit(self,app,max_jobs,*,confirm=False):
        if confirm is not True:raise PermissionError('Only local owner can set parallel project limits.')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            result=parallel.app_limit(db,app,max_jobs)
            db.commit()
        return result

    def inventory(self):
        """Dashboard-only safe metadata: never expose tokens or queued payloads."""
        with self._db() as db:
            rows=db.execute('SELECT a.app,a.tasks,a.enabled,j.status,COUNT(j.id) AS amount '
                            'FROM apps a LEFT JOIN jobs j ON j.app=a.app '
                            'GROUP BY a.app,j.status ORDER BY a.app').fetchall()
        apps={}
        for row in rows:
            item=apps.setdefault(row['app'],{'app':row['app'],
                'tasks':json.loads(row['tasks']),'enabled':bool(row['enabled']),'queues':{}})
            if row['status']:
                item['queues'][row['status']]=row['amount']
        with self._db() as db:
            nodes=[parallel.snapshot(db,row['id']) for row in db.execute(
                'SELECT id FROM compute_nodes WHERE enabled=1 ORDER BY id').fetchall()]
        return {'apps':list(apps.values()),'nodes':nodes,'runtime':'app-worker-broker',
                'resource_mode':'reservations' if nodes else 'legacy-unlimited',
                'resource_enforcement':'not-os-enforced',
                'execute_untrusted_code':False}

    def overview(self,who):
        with self._db() as db:
            rows=db.execute('SELECT status,COUNT(*) AS count FROM jobs WHERE app=? GROUP BY status',
                            (who['app'],)).fetchall()
        return {'app':who['app'],'scope':who['role'],'tasks':who['tasks'],
                'queues':{row['status']:row['count'] for row in rows},
                'execution':'external-approved-workers',
                'arbitrary_host_commands':False}

def main(argv=None):
    parser=argparse.ArgumentParser(description='Owner-only local app/worker setup; never run in CI jobs.')
    parser.add_argument('--state',type=Path)
    sub=parser.add_subparsers(dest='cmd',required=True)
    reg=sub.add_parser('register');reg.add_argument('app');reg.add_argument('--tasks',required=True)
    reg.add_argument('--confirm',action='store_true')
    issue=sub.add_parser('issue');issue.add_argument('app');issue.add_argument('role',choices=sorted(ROLE))
    issue.add_argument('--confirm',action='store_true')
    revoke=sub.add_parser('revoke');revoke.add_argument('sha256');revoke.add_argument('--confirm',action='store_true')
    args=parser.parse_args(argv)
    try:
        broker=Broker(args.state)
        if args.cmd=='register':out=broker.register(args.app,args.tasks.split(','),confirm=args.confirm)
        elif args.cmd=='issue':out=broker.issue(args.app,args.role,confirm=args.confirm)
        else:out=broker.revoke(args.sha256,confirm=args.confirm)
        print(json.dumps(out,indent=2));return 0
    except (PermissionError,RuntimeError,ValueError,OSError,sqlite3.Error) as exc:
        print('App broker:',str(exc)[:200],file=sys.stderr);return 1

if __name__=='__main__':
    raise SystemExit(main())
