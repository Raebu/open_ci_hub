"""Owner controls and durable CI evidence in the existing shared job ledger."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import time

from app_runtime import JOB
from ci_execution import redact


def setup(broker):
    with broker._db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS ci_bridge_events(
            job_id TEXT PRIMARY KEY REFERENCES jobs(id),provider TEXT NOT NULL,repo TEXT NOT NULL,
            sha TEXT NOT NULL,branch TEXT NOT NULL,event TEXT NOT NULL,status TEXT NOT NULL,
            detail TEXT,created REAL NOT NULL,updated REAL NOT NULL)""")
        db.execute('''CREATE TABLE IF NOT EXISTS ci_job_controls (
            job_id TEXT PRIMARY KEY REFERENCES jobs(id), cancel_requested INTEGER NOT NULL DEFAULT 0,
            actor TEXT, requested REAL, provider_reported INTEGER,
            receipt TEXT, finished REAL)''')


def check_job(job):
    if not isinstance(job, str) or not JOB.fullmatch(job):
        raise ValueError('Invalid CI job ID')


def cancel(broker, job, actor):
    check_job(job); setup(broker)
    if not isinstance(actor, str) or not actor or len(actor) > 120:
        raise ValueError('Authenticated actor required')
    with broker._db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT j.status FROM jobs j JOIN ci_bridge_events e ON j.id=e.job_id WHERE j.id=?', (job,)).fetchone()
        if not row:
            raise PermissionError('Unknown CI job')
        state = row['status']
        if state not in ('queued', 'leased', 'stranded'):
            return {'job_id': job, 'status': state, 'unchanged': True}
        db.execute('INSERT INTO ci_job_controls(job_id,cancel_requested,actor,requested) VALUES(?,1,?,?) '
                   'ON CONFLICT(job_id) DO UPDATE SET cancel_requested=1,actor=excluded.actor,requested=excluded.requested',
                   (job, actor, time.time()))
        if state == 'queued':
            db.execute('UPDATE jobs SET status="cancelled",updated=? WHERE id=?', (time.time(), job))
            db.execute('UPDATE ci_bridge_events SET status="cancelled",detail="Cancelled before execution",updated=? WHERE job_id=?', (time.time(), job))
            state = 'cancelled'
        db.commit()
    return {'job_id': job, 'status': state, 'cancel_requested': True,
            'termination_confirmed': state == 'cancelled'}


def cancellation_requested(broker, job):
    with broker._db() as db:
        row = db.execute('SELECT cancel_requested FROM ci_job_controls WHERE job_id=?', (job,)).fetchone()
    return bool(row and row['cancel_requested'])


def finish_cancel(broker, who, job, token):
    with broker._db() as db:
        db.execute('BEGIN IMMEDIATE')
        broker._leased(db, who, job, token)
        db.execute('UPDATE jobs SET status="cancelled",lease_hash=NULL,lease_until=NULL,updated=?,result=? WHERE id=?',
                   (time.time(), json.dumps({'status':'cancelled','termination_confirmed':True}), job))
        db.commit()
    return {'job_id':job,'status':'cancelled'}


def save_receipt(broker, job, receipt):
    check_job(job)
    wire = json.dumps(receipt, sort_keys=True, ensure_ascii=True, allow_nan=False)
    if len(wire) > 32000:
        raise ValueError('CI receipt exceeds size limit')
    with broker._db() as db:
        db.execute('INSERT INTO ci_job_controls(job_id,receipt,finished,provider_reported) VALUES(?,?,?,?) '
                   'ON CONFLICT(job_id) DO UPDATE SET receipt=excluded.receipt,finished=excluded.finished,provider_reported=excluded.provider_reported',
                   (job, wire, time.time(), int(receipt['provider_status_reported'])))


def inspect(broker, state, job):
    check_job(job); setup(broker)
    with broker._db() as db:
        row = db.execute('SELECT e.*,j.status AS scheduler_status,j.result,j.attempts,c.cancel_requested,c.actor,c.receipt '
                         'FROM ci_bridge_events e JOIN jobs j ON j.id=e.job_id '
                         'LEFT JOIN ci_job_controls c ON c.job_id=e.job_id WHERE j.id=?', (job,)).fetchone()
    if not row:
        raise PermissionError('Unknown CI job')
    out = dict(row)
    out['receipt'] = json.loads(out['receipt']) if out['receipt'] else None
    path = Path(state) / 'logs' / (job + '.log')
    out['log'] = ''
    if path.is_symlink():
        raise PermissionError('Unsafe CI log')
    if path.is_file():
        with path.open('rb') as stream:
            stream.seek(0, 2); length = stream.tell(); stream.seek(max(0, length-32000))
            out['log'] = redact(stream.read(32000).decode('utf-8','replace'))
        out['log_bytes'] = length
        out['log_truncated'] = length > 32000
    return out


def collect_artifacts(work, destination, names, max_bytes=20*1024*1024):
    """Copy only declared regular files; reject symlinks including parent components."""
    import stat
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    found = []; total = 0
    for name in names:
        parts = Path(name).parts
        if not parts or Path(name).is_absolute() or any(p in ('.','..','.git') for p in parts):
            raise ValueError('Unsafe artifact path')
        source = Path(work)
        for part in parts:
            source = source / part
            if source.is_symlink():
                raise PermissionError('Artifact symlinks are forbidden')
        with source.open('rb') as stream:
            meta = os.fstat(stream.fileno())
            if not stat.S_ISREG(meta.st_mode):
                raise PermissionError('Artifact must be a regular file')
            total += meta.st_size
            if total > max_bytes:
                raise RuntimeError('Artifact size quota exceeded')
            data = stream.read(meta.st_size+1)
            if len(data) != meta.st_size:
                raise RuntimeError('Artifact changed during collection')
        target = destination / hashlib.sha256(name.encode()).hexdigest()
        fd = os.open(target, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd,'wb') as stream:
            stream.write(data)
        found.append({'path':name,'file':target.name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    return found
