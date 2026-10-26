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
from resource_guard import ResourceGate
import fleet_devices

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
            ResourceGate.setup(db)
            fleet_devices.setup(db)
            cols={x['name'] for x in db.execute('PRAGMA table_info(jobs)')}
            if 'priority' not in cols:
                db.execute('ALTER TABLE jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0')
        self.resource_gate=ResourceGate()
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

    def issue(self,app,role,*,confirm=False):
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
            db.execute('INSERT INTO credentials(token_hash,app,role,created) VALUES(?,?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(),app,role,time.time()))
            db.commit()
        return {'app':app,'scope':role,'token':token,
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
            row=db.execute('SELECT c.token_hash,c.app,c.role,a.tasks FROM credentials c JOIN apps a ON a.app=c.app '
                           'WHERE c.token_hash=? AND c.revoked=0 AND a.enabled=1',(digest,)).fetchone()
        if not row or not hmac.compare_digest(row['token_hash'],digest):
            raise PermissionError('Application credential was not recognised.')
        return {'app':row['app'],'role':row['role'],'tasks':json.loads(row['tasks']),
                'credential_hash':digest}

    def enroll_edge(self,app,kind,*,confirm=False):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            receipt=fleet_devices.enroll(db,app,kind,confirm=confirm)
            db.commit()
        return receipt

    def rebind_edge(self,app,token_hash,*,confirm=False):
        app=self._check_app(app)
        if confirm is not True:
            raise PermissionError("Owner confirmation required to rebind a device credential.")
        if not isinstance(token_hash,str) or not re.fullmatch(r"[a-f0-9]{64}",token_hash):
            raise ValueError("Expected a SHA-256 worker-token hash.")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                app_row=db.execute("SELECT tasks,enabled FROM apps WHERE app=?",(app,)).fetchone()
                devices=db.execute("SELECT credential_hash,kind,allowed_tasks,approved FROM fleet_devices WHERE app=?",(app,)).fetchall()
                workers=db.execute("SELECT token_hash FROM credentials WHERE app=? AND role=? AND revoked=0",(app,"worker")).fetchall()
                if not app_row or app_row["enabled"]!=1:
                    raise ValueError("Application is not enabled.")
                tasks=json.loads(app_row["tasks"])
                if not tasks or not set(tasks)<=fleet_devices.SUPPORTED:
                    raise ValueError("Application includes tasks not approved for remote devices.")
                if len(devices)!=1 or devices[0]["approved"]!=1 or devices[0]["kind"] not in fleet_devices.KINDS:
                    raise RuntimeError("Exactly one approved device is required.")
                if set(json.loads(devices[0]["allowed_tasks"]))!=set(tasks):
                    raise RuntimeError("Device task scope does not match the application.")
                if len(workers)!=1 or workers[0]["token_hash"]!=devices[0]["credential_hash"]:
                    raise RuntimeError("Existing worker credential is not uniquely bound to the device.")
                if db.execute("SELECT 1 FROM credentials WHERE token_hash=?",(token_hash,)).fetchone():
                    raise ValueError("This credential hash has already been used.")
                active=db.execute("SELECT COUNT(*) FROM jobs WHERE app=? AND status IN (?,?,?)",(app,"queued","leased","stranded")).fetchone()[0]
                if active:
                    raise RuntimeError("Queued, leased or stranded jobs exist; device rebinding is paused.")
                old_hash=workers[0]["token_hash"]
                db.execute("UPDATE credentials SET revoked=1 WHERE token_hash=? AND app=? AND role=? AND revoked=0",(old_hash,app,"worker"))
                db.execute("INSERT INTO credentials(token_hash,app,role,created,revoked) VALUES(?,?,?,?,0)",(token_hash,app,"worker",time.time()))
                changed=db.execute("UPDATE fleet_devices SET credential_hash=?,free_ram_mb=NULL,charging=NULL,last_seen=0 WHERE app=? AND credential_hash=? AND approved=1",(token_hash,app,old_hash)).rowcount
                if changed!=1:
                    raise RuntimeError("Device credential binding changed unexpectedly.")
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {"app":app,"rebound":True,"previous_worker_revoked":True,"device_health":"requires_fresh_report"}

    def edge_availability(self,who,data):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            result=fleet_devices.report(db,who,data)
            db.commit()
        return result

    def edge_devices(self):
        with self._db() as db:
            return fleet_devices.listing(db)

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

    def set_task_budget(self,app,task,ram_mb,vram_mb=0,gpu_index=None,*,confirm=False):
        app=self._check_app(app)
        if confirm is not True:
            raise PermissionError('Only local host owner can approve a task resource budget.')
        ResourceGate.validate_profile(ram_mb,vram_mb,gpu_index)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT tasks FROM apps WHERE app=?',(app,)).fetchone()
            if not row or task not in json.loads(row['tasks']):
                raise PermissionError('Only pre-registered app tasks may have resource budgets.')
            active=db.execute('SELECT COUNT(*) FROM jobs WHERE app=? AND task=? '
                              'AND status IN ("leased","stranded")',(app,task)).fetchone()[0]
            if active:
                raise RuntimeError('Wait for task leases and stranded reservations to clear before changing budgets.')
            ResourceGate.set_profile(db,app,task,ram_mb,vram_mb,gpu_index)
            db.commit()
        return {'app':app,'task':task,'ram_mb':ram_mb,
                'vram_mb':vram_mb,'gpu_index':gpu_index}

    def set_parallel(self,app,parallel,*,confirm=False):
        app=self._check_app(app)
        if confirm is not True:
            raise PermissionError('Owner must approve app parallel capacity.')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM apps WHERE app=?',(app,)).fetchone():
                raise PermissionError('App not registered.')
            ResourceGate.set_app_limit(db,app,parallel)
            db.commit()
        return {'app':app,'max_parallel':parallel}

    def set_global_resources(self,max_parallel,min_free_ram_mb,min_free_vram_mb,*,confirm=False):
        if confirm is not True:
            raise PermissionError('Host owner must approve global memory and GPU headroom.')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            ResourceGate.set_config(db,max_parallel=max_parallel,
                min_free_ram_mb=min_free_ram_mb,min_free_vram_mb=min_free_vram_mb)
            db.commit()
        return {'max_parallel':max_parallel,'min_free_ram_mb':min_free_ram_mb,
                'min_free_vram_mb':min_free_vram_mb}

    def resource_status(self):
        with self._db() as db:
            return self.resource_gate.snapshot(db)

    def recover_stranded(self,job,*,confirm=False):
        if confirm is not True:
            raise PermissionError('The host owner must confirm the original worker process has stopped.')
        if not isinstance(job,str) or not JOB.fullmatch(job):
            raise ValueError('Invalid job ID.')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT attempts,status FROM jobs WHERE id=?',(job,)).fetchone()
            if not row or row['status']!='stranded':
                raise RuntimeError('No stranded reservation with this ID.')
            new_status='queued' if row['attempts']<MAX_ATTEMPTS else 'failed'
            db.execute('UPDATE jobs SET status=?,lease_hash=NULL,lease_until=NULL,updated=?,'
                       'result=? WHERE id=?',(new_status,time.time(),
                       json.dumps({'message':'Host owner confirmed stranded worker stopped.'}),job))
            db.commit()
        return {'job_id':job,'status':new_status}

    def control(self,mode=None,paused=None,*,confirm=False):
        if confirm is not True:
            raise PermissionError('Explicit owner/editor confirmation required.')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            result=ResourceGate.set_controls(db,mode=mode,paused=paused)
            db.commit()
        return result

    def reconcile_stranded(self):
        """Release stranded capacity only after host systemd proves scope inactive.

        Never auto-retry side-effecting jobs: mark failed. Workers must submit
        idempotent new work if/when an owner or application decides to retry.
        """
        import subprocess
        with self._db() as db:
            rows=db.execute('SELECT id FROM jobs WHERE status="stranded" LIMIT 100').fetchall()
        cleared=[];unverified=[]
        for row in rows:
            job=row['id']
            unit='open-ci-app-'+job+'.scope'
            try:
                observed=subprocess.run(['/usr/bin/systemctl','--user','--no-ask-password',
                    'show',unit,'--property=ActiveState','--property=ControlGroup',
                    '--property=LoadState','--no-pager'],
                    capture_output=True,text=True,timeout=5,check=False)
                props=dict(line.split('=',1) for line in observed.stdout.splitlines() if '=' in line)
                safe=(observed.returncode==0 and
                    props.get('ActiveState') in ('inactive','failed') and
                    props.get('ControlGroup')=='' and
                    props.get('LoadState') in ('loaded','not-found'))
            except (OSError,subprocess.TimeoutExpired,ValueError):
                safe=False
            if not safe:
                unverified.append(job)
                continue
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                changed=db.execute('UPDATE jobs SET status="failed",lease_hash=NULL,'
                    'lease_until=NULL,updated=?,result=? WHERE id=? AND status="stranded"',
                    (time.time(),json.dumps({'error':'Original systemd app scope confirmed '
                    'inactive. Side effects not retried automatically.'}),job)).rowcount
                db.commit()
            if changed:cleared.append(job)
        return {'verified_failed':cleared,'still_stranded':unverified,
                'automatic_retry':False,'reason':'At-least-once effects require app-level idempotency.'}

    def watchdog_once(self):
        """No new work under critical laptop pressure, without touch to CI jobs."""
        snapshot=self.resource_status()
        policy=snapshot['policy']
        if policy['max_parallel']==0 and policy['reason'] in (
            'critical_thermal','critical_memory_pressure','low_battery'
        ) and not policy['paused']:
            return {'critical':policy['reason'],
                    'stop':self.emergency_stop(confirm=True),
                    'recovery':self.reconcile_stranded()}
        return {'critical':None,'recovery':self.reconcile_stranded()}

    def emergency_stop(self,*,confirm=False):
        """Stop all *managed application scope units*; do not touch CI jobs or desktop.

        This is a host-local systemd operation. Incomplete kills remain visibly
        unresolved and all leases stay stranded until verified safe recovery.
        """
        if confirm is not True:
            raise PermissionError('Explicit emergency action approval required.')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            ResourceGate.set_controls(db,paused=True)
            jobs=db.execute('SELECT id FROM jobs WHERE status IN ("leased","stranded")').fetchall()
            db.execute('UPDATE jobs SET status="stranded" WHERE status="leased"')
            db.commit()
        import subprocess
        outcomes=[]
        for row in jobs:
            unit='open-ci-app-'+row['id']+'.scope'
            try:
                stopped=subprocess.run(['/usr/bin/systemctl','--user','--no-ask-password',
                    'stop',unit],timeout=12,stdin=subprocess.DEVNULL,
                    capture_output=True,check=False)
                # A successful stop request is not proof that children are dead.
                probe=subprocess.run(['/usr/bin/systemctl','--user','--no-ask-password',
                    'is-active',unit],timeout=5,stdin=subprocess.DEVNULL,
                    capture_output=True,check=False)
                confirmed=stopped.returncode==0 and probe.returncode!=0
            except (OSError,subprocess.TimeoutExpired):
                confirmed=False
            outcomes.append({'job_id':row['id'],'stop_confirmed':confirmed})
        return {'paused':True,'target':'managed_app_scopes_only',
                'stop_results':outcomes,
                'unconfirmed':sum(not x['stop_confirmed'] for x in outcomes),
                'leases_stranded':True,
                'message':'New app jobs are paused. All running leases remain stranded until separately recovered.'}

    def reprioritize(self,job,priority,*,confirm=False):
        if confirm is not True:
            raise PermissionError('Explicit job priority confirmation required.')
        if not isinstance(job,str) or not JOB.fullmatch(job) or type(priority)!=int or not -5<=priority<=5:
            raise ValueError('Invalid job ID or priority (-5 to 5).')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT app,task,status FROM jobs WHERE id=?',(job,)).fetchone()
            if not row or row['status']!='queued':
                raise RuntimeError('Only queued jobs can be reprioritised.')
            db.execute('UPDATE jobs SET priority=? WHERE id=?',(priority,job))
            db.commit()
        return {'job_id':job,'app':row['app'],'priority':priority}

    def queue_preview(self):
        with self._db() as db:
            rows=db.execute('SELECT id,app,task,priority,created,status FROM jobs '
                            'WHERE status IN ("queued","leased","stranded") '
                            'ORDER BY priority DESC,created,id LIMIT 60').fetchall()
        return {'jobs':[{'job_id':r['id'],'app':r['app'],'task':r['task'],
                         'priority':r['priority'],'created':r['created'],
                         'status':r['status']} for r in rows]}

    def claim(self,who):
        self._require(who,'worker')
        now=time.time();app=who['app']
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            # Expired worker may STILL be running. Never reassign RAM/VRAM or
            # repeat side effects until host owner confirms process termination.
            db.execute('UPDATE jobs SET status="stranded",updated=?,'
                       'result=? WHERE status="leased" AND lease_until<=?',
                       (now,json.dumps({'error':'Lease expired; original worker may still be running. '
                           'Host owner must verify termination before recovery.'}),now))
            is_edge=db.execute('SELECT 1 FROM fleet_devices WHERE app=?',(app,)).fetchone() is not None
            snapshot=None if is_edge else self.resource_gate.snapshot(db)
            candidates=db.execute('SELECT id,task,payload,attempts FROM jobs '
                                  'WHERE app=? AND status="queued" '
                                  'ORDER BY priority DESC,created,id LIMIT 100',(app,)).fetchall()
            if not candidates:
                db.commit()
                return {'job':None,'reason':'queue_empty'}
            selected=None;budget=None;blocked='capacity_exhausted'
            for row in candidates:
                if is_edge:
                    profile=self.resource_gate.profile(db,app,row['task'])
                    reason=fleet_devices.admit(db,who,row['task'],profile['ram_mb'])
                    controls=self.resource_gate.controls(db)
                    active=db.execute('SELECT COUNT(*) FROM jobs WHERE app=? '
                        'AND status IN ("leased","stranded")',(app,)).fetchone()[0]
                    if controls['paused']:
                        reason='admissions_paused'
                    elif active>=self.resource_gate.app_limit(db,app):
                        reason='remote_device_parallel_limit'
                    if reason=='ready' and profile['vram_mb']!=0:
                        reason='remote_gpu_jobs_forbidden'
                    if reason=='ready':
                        selected,budget=row,profile
                        break
                else:
                    profile,reason=self.resource_gate.admit(db,app,row['task'],snapshot)
                    if profile:
                        selected,budget=row,profile
                        break
                blocked=reason
            if selected is None:
                db.commit()
                return {'job':None,'reason':blocked}
            secret=secrets.token_urlsafe(36)
            db.execute('UPDATE jobs SET status="leased",attempts=attempts+1,lease_hash=?,'
                       'lease_until=?,updated=? WHERE id=?',
                       (hashlib.sha256(secret.encode()).hexdigest(),
                        now+LEASE_SECONDS,now,selected['id']))
            db.commit()
        return {'job':{'job_id':selected['id'],'task':selected['task'],
                       'payload':json.loads(selected['payload']),
                       'attempt':selected['attempts']+1,'lease_token':secret,
                       'lease_seconds':LEASE_SECONDS,'resources':budget,
                       'requires_ram_enforcement':True,
                       'vram_guard':'reservation-and-telemetry-not-hardware-limit'}}

    def _leased(self,db,who,job,lease_token):
        self._require(who,'worker')
        if not isinstance(job,str) or not JOB.fullmatch(job) or (
            not isinstance(lease_token,str) or len(lease_token)<40 or len(lease_token)>100):
            raise PermissionError('Invalid job lease.')
        row=db.execute('SELECT status,lease_until,lease_hash,attempts FROM jobs '
                       'WHERE app=? AND id=?',(who['app'],job)).fetchone()
        digest=hashlib.sha256(lease_token.encode()).hexdigest()
        if (not row or row['status']!='leased' or not isinstance(row['lease_until'],float)
                or row['lease_until']<=time.time() or not row['lease_hash']
                or not hmac.compare_digest(row['lease_hash'],digest)):
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

    def complete(self,who,job,lease_token,success,result,*,allow_retry=True):
        if type(success) is not bool:raise ValueError('Explicit success boolean is required.')
        output=self._json_value(result,MAX_RESULT)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            state=self._leased(db,who,job,lease_token)
            status='completed' if success else ('queued' if allow_retry and state['attempts']<MAX_ATTEMPTS else 'failed')
            db.execute('UPDATE jobs SET status=?,result=?,lease_hash=NULL,lease_until=NULL,'
                       'updated=? WHERE id=?',
                       (status,output,time.time(),job))
            db.commit()
        return {'job_id':job,'status':status,'attempts':state['attempts']}

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
            for item in apps.values():
                item['max_parallel']=self.resource_gate.app_limit(db,item['app'])
            resources=self.resource_gate.snapshot(db)
        return {'apps':list(apps.values()),'resources':resources,
                'fleet_devices':self.edge_devices(),
                'runtime':'parallel-admission-broker',
                'execute_untrusted_code':False}

    def overview(self,who):
        with self._db() as db:
            rows=db.execute('SELECT status,COUNT(*) AS count FROM jobs WHERE app=? GROUP BY status',
                            (who['app'],)).fetchall()
        with self._db() as db:
            resources=self.resource_gate.snapshot(db)
            app_limit=self.resource_gate.app_limit(db,who['app'])
        return {'app':who['app'],'scope':who['role'],'tasks':who['tasks'],
                'queues':{row['status']:row['count'] for row in rows},
                'max_parallel':app_limit,
                'capacity':resources['capacity'],
                'execution':'external-approved-workers',
                'arbitrary_host_commands':False}

def main(argv=None):
    parser=argparse.ArgumentParser(description='Owner-only local app/worker setup; never run in CI jobs.')
    parser.add_argument('--state',type=Path)
    sub=parser.add_subparsers(dest='cmd',required=True)
    reg=sub.add_parser('register');reg.add_argument('app');reg.add_argument('--tasks',required=True)
    reg.add_argument('--confirm',action='store_true')
    edge=sub.add_parser('edge-enroll');edge.add_argument('app')
    edge.add_argument('--kind',choices=sorted(fleet_devices.KINDS),required=True)
    edge.add_argument('--confirm',action='store_true')
    sub.add_parser('edge-list')
    rebind=sub.add_parser('edge-rebind');rebind.add_argument('app');rebind.add_argument('sha256')
    rebind.add_argument('--confirm',action='store_true')
    issue=sub.add_parser('issue');issue.add_argument('app');issue.add_argument('role',choices=sorted(ROLE))
    issue.add_argument('--confirm',action='store_true')
    revoke=sub.add_parser('revoke');revoke.add_argument('sha256');revoke.add_argument('--confirm',action='store_true')
    budget=sub.add_parser('budget');budget.add_argument('app');budget.add_argument('task')
    budget.add_argument('--ram-mb',type=int,required=True)
    budget.add_argument('--vram-mb',type=int,default=0)
    budget.add_argument('--gpu-index',type=int)
    budget.add_argument('--confirm',action='store_true')
    parallel=sub.add_parser('parallel');parallel.add_argument('app')
    parallel.add_argument('--max-parallel',type=int,required=True)
    parallel.add_argument('--confirm',action='store_true')
    global_policy=sub.add_parser('global-resources')
    global_policy.add_argument('--max-parallel',type=int,required=True)
    global_policy.add_argument('--min-free-ram-mb',type=int,required=True)
    global_policy.add_argument('--min-free-vram-mb',type=int,required=True)
    global_policy.add_argument('--confirm',action='store_true')
    recover=sub.add_parser('recover-stranded');recover.add_argument('job_id')
    recover.add_argument('--confirm',action='store_true')
    sub.add_parser('resources')
    sub.add_parser('reconcile')
    sub.add_parser('watchdog')
    pause=sub.add_parser('pause');pause.add_argument('--confirm',action='store_true')
    resume=sub.add_parser('resume');resume.add_argument('--confirm',action='store_true')
    mode=sub.add_parser('mode');mode.add_argument('name',choices=['quiet','balanced','performance'])
    mode.add_argument('--confirm',action='store_true')
    emergency=sub.add_parser('emergency-stop');emergency.add_argument('--confirm',action='store_true')
    priority=sub.add_parser('priority');priority.add_argument('job_id')
    priority.add_argument('value',type=int);priority.add_argument('--confirm',action='store_true')
    args=parser.parse_args(argv)
    try:
        broker=Broker(args.state)
        if args.cmd=='register':out=broker.register(args.app,args.tasks.split(','),confirm=args.confirm)
        elif args.cmd=='edge-enroll':out=broker.enroll_edge(args.app,args.kind,confirm=args.confirm)
        elif args.cmd=='edge-list':out=broker.edge_devices()
        elif args.cmd=='edge-rebind':out=broker.rebind_edge(args.app,args.sha256,confirm=args.confirm)
        elif args.cmd=='issue':out=broker.issue(args.app,args.role,confirm=args.confirm)
        elif args.cmd=='revoke':out=broker.revoke(args.sha256,confirm=args.confirm)
        elif args.cmd=='budget':out=broker.set_task_budget(args.app,args.task,
            args.ram_mb,args.vram_mb,args.gpu_index,confirm=args.confirm)
        elif args.cmd=='parallel':out=broker.set_parallel(args.app,args.max_parallel,
            confirm=args.confirm)
        elif args.cmd=='global-resources':out=broker.set_global_resources(
            args.max_parallel,args.min_free_ram_mb,args.min_free_vram_mb,confirm=args.confirm)
        elif args.cmd=='recover-stranded':out=broker.recover_stranded(args.job_id,confirm=args.confirm)
        elif args.cmd=='pause':out=broker.control(paused=True,confirm=args.confirm)
        elif args.cmd=='resume':out=broker.control(paused=False,confirm=args.confirm)
        elif args.cmd=='mode':out=broker.control(mode=args.name,confirm=args.confirm)
        elif args.cmd=='priority':out=broker.reprioritize(args.job_id,args.value,confirm=args.confirm)
        elif args.cmd=='emergency-stop':out=broker.emergency_stop(confirm=args.confirm)
        elif args.cmd=='reconcile':out=broker.reconcile_stranded()
        elif args.cmd=='watchdog':out=broker.watchdog_once()
        else:out=broker.resource_status()
        print(json.dumps(out,indent=2));return 0
    except (PermissionError,RuntimeError,ValueError,OSError,sqlite3.Error) as exc:
        print('App broker:',str(exc)[:200],file=sys.stderr);return 1

if __name__=='__main__':
    raise SystemExit(main())
