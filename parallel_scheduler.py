"""Transactional, bounded parallel scheduling for approved application workers.

Reservations are admission control, NOT OS-enforced RAM or GPU VRAM limits.
Workers must separately sandbox processes and terminate stale leases.
"""
from __future__ import annotations
import json
import re
import sqlite3
import time

NODE_ID = re.compile(r'^[a-z][a-z0-9-]{1,50}$')
MAX_RAM = 1024 * 1024
MAX_VRAM = 512 * 1024
MAX_CPU = 256
MAX_JOBS = 64

def _whole(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer from {low} to {high}.')
    return value

def init_schema(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS compute_nodes (
        id TEXT PRIMARY KEY, memory_mb INTEGER NOT NULL, vram_mb INTEGER NOT NULL,
        cpu_slots INTEGER NOT NULL, max_jobs INTEGER NOT NULL,
        reserve_memory_mb INTEGER NOT NULL, reserve_vram_mb INTEGER NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS task_resources (
        app TEXT NOT NULL, task TEXT NOT NULL,
        memory_mb INTEGER NOT NULL, vram_mb INTEGER NOT NULL,
        cpu_slots INTEGER NOT NULL, PRIMARY KEY (app,task),
        FOREIGN KEY(app) REFERENCES apps(app)
    );
    CREATE TABLE IF NOT EXISTS app_parallel_limits (
        app TEXT PRIMARY KEY, max_jobs INTEGER NOT NULL,
        FOREIGN KEY(app) REFERENCES apps(app)
    );
    """)
    # Safe additive migrations for existing app_runtime.py SQLite installations.
    for table, columns in {
        'credentials': {'node_id':'TEXT'},
        'jobs': {'reserved_node':'TEXT','reserved_memory_mb':'INTEGER NOT NULL DEFAULT 0',
                 'reserved_vram_mb':'INTEGER NOT NULL DEFAULT 0',
                 'reserved_cpu_slots':'INTEGER NOT NULL DEFAULT 0'}
    }.items():
        existing={row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
        for column, decl in columns.items():
            if column not in existing:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {decl}')
    db.execute('CREATE INDEX IF NOT EXISTS idx_parallel_active ON jobs(reserved_node,status,lease_until)')

def enabled(db):
    return db.execute('SELECT 1 FROM compute_nodes LIMIT 1').fetchone() is not None

def register_node(db, node, memory_mb, vram_mb, cpu_slots, max_jobs,
                  reserve_memory_mb=None, reserve_vram_mb=None):
    if not isinstance(node,str) or not NODE_ID.fullmatch(node):
        raise ValueError('Invalid machine ID.')
    _whole(memory_mb,1024,MAX_RAM,'Memory MB')
    _whole(vram_mb,0,MAX_VRAM,'VRAM MB')
    _whole(cpu_slots,1,MAX_CPU,'CPU slots')
    _whole(max_jobs,1,MAX_JOBS,'Max parallel jobs')
    # A deliberate safety buffer must remain free for desktop and the OS.
    if reserve_memory_mb is None:reserve_memory_mb=max(1024,(memory_mb+3)//4)
    if reserve_vram_mb is None:reserve_vram_mb=max(256,(vram_mb+7)//8) if vram_mb else 0
    _whole(reserve_memory_mb,1024,memory_mb-256,'OS memory reserve MB')
    if vram_mb:
        _whole(reserve_vram_mb,256,vram_mb-1,'GPU VRAM reserve MB')
    elif reserve_vram_mb!=0:
        raise ValueError('CPU-only nodes must reserve zero VRAM.')
    if memory_mb-reserve_memory_mb<256:
        raise ValueError('At least 256 MB worker RAM required.')
    old=db.execute('SELECT id FROM compute_nodes WHERE id=?',(node,)).fetchone()
    if old:raise RuntimeError('Node already configured; refuse silent capacity changes.')
    db.execute('INSERT INTO compute_nodes(id,memory_mb,vram_mb,cpu_slots,max_jobs,'
               'reserve_memory_mb,reserve_vram_mb) VALUES(?,?,?,?,?,?,?)',
               (node,memory_mb,vram_mb,cpu_slots,max_jobs,reserve_memory_mb,reserve_vram_mb))
    return {'node':node,'memory_mb':memory_mb,'vram_mb':vram_mb,
            'cpu_slots':cpu_slots,'max_jobs':max_jobs,
            'reserve_memory_mb':reserve_memory_mb,'reserve_vram_mb':reserve_vram_mb}

def set_task(db,app,task,memory_mb,vram_mb,cpu_slots):
    if not isinstance(task,str) or not re.fullmatch(r'[a-z][a-z0-9_.-]{1,64}',task):
        raise ValueError('Invalid task name.')
    _whole(memory_mb,64,MAX_RAM,'Task RAM MB')
    _whole(vram_mb,0,MAX_VRAM,'Task VRAM MB')
    _whole(cpu_slots,1,MAX_CPU,'Task CPU slots')
    row=db.execute('SELECT tasks FROM apps WHERE app=? AND enabled=1',(app,)).fetchone()
    if not row or task not in json.loads(row['tasks']):
        raise PermissionError('Task must be registered and enabled for this application.')
    existing=db.execute('SELECT 1 FROM task_resources WHERE app=? AND task=?',(app,task)).fetchone()
    if existing:
        # Updating a live workload profile can invalidate its accounting.
        live=db.execute('SELECT 1 FROM jobs WHERE app=? AND task=? AND status="leased" '
                        'AND lease_until>? LIMIT 1',(app,task,time.time())).fetchone()
        if live:raise RuntimeError('Cannot change the resource profile while a task is running.')
    db.execute('INSERT INTO task_resources(app,task,memory_mb,vram_mb,cpu_slots) '
               'VALUES(?,?,?,?,?) ON CONFLICT(app,task) DO UPDATE SET '
               'memory_mb=excluded.memory_mb,vram_mb=excluded.vram_mb,cpu_slots=excluded.cpu_slots',
               (app,task,memory_mb,vram_mb,cpu_slots))
    return {'app':app,'task':task,'memory_mb':memory_mb,'vram_mb':vram_mb,'cpu_slots':cpu_slots}

def app_limit(db,app,max_jobs):
    _whole(max_jobs,1,MAX_JOBS,'Max parallel app jobs')
    if not db.execute('SELECT 1 FROM apps WHERE app=?',(app,)).fetchone():
        raise PermissionError('Application not registered.')
    live=db.execute('SELECT COUNT(*) FROM jobs WHERE app=? AND status="leased" AND lease_until>?',
                    (app,time.time())).fetchone()[0]
    if live>max_jobs:
        raise RuntimeError('New app parallel limit is below in-flight work.')
    db.execute('INSERT INTO app_parallel_limits(app,max_jobs) VALUES(?,?) '
               'ON CONFLICT(app) DO UPDATE SET max_jobs=excluded.max_jobs',(app,max_jobs))
    return {'app':app,'max_parallel_jobs':max_jobs}

def validate_telemetry(metrics,needs_gpu):
    # These observations are supplied by authenticated app workers, NOT independently attested.
    if not isinstance(metrics,dict):
        raise ValueError('Worker memory observations required for scheduled execution.')
    observed={}
    for key,high in (('memory_available_mb',MAX_RAM),('vram_available_mb',MAX_VRAM)):
        n=metrics.get(key)
        if key=='vram_available_mb' and not needs_gpu and n is None:n=0
        observed[key]=_whole(n,0,high,key)
    measured_at=metrics.get('observed_at')
    if type(measured_at) not in (int,float) or abs(time.time()-measured_at)>30:
        raise ValueError('Fresh local worker resource observations required (30 seconds).')
    return observed

def snapshot(db,node,now=None):
    now=time.time() if now is None else now
    row=db.execute('SELECT * FROM compute_nodes WHERE id=?',(node,)).fetchone()
    if not row or not row['enabled']:
        raise PermissionError('Node is missing or disabled.')
    totals=db.execute('SELECT COALESCE(SUM(reserved_memory_mb),0) AS memory,'
                      'COALESCE(SUM(reserved_vram_mb),0) AS vram,'
                      'COALESCE(SUM(reserved_cpu_slots),0) AS cpu,COUNT(*) AS jobs '
                      'FROM jobs WHERE reserved_node=? AND status="leased" AND lease_until>?',
                      (node,now)).fetchone()
    return {'node':node,'memory_mb':row['memory_mb'],'vram_mb':row['vram_mb'],
            'reserve_memory_mb':row['reserve_memory_mb'],
            'reserve_vram_mb':row['reserve_vram_mb'],
            'available_memory_mb':max(0,row['memory_mb']-row['reserve_memory_mb']-totals['memory']),
            'available_vram_mb':max(0,row['vram_mb']-row['reserve_vram_mb']-totals['vram']),
            'available_cpu_slots':max(0,row['cpu_slots']-totals['cpu']),
            'available_job_slots':max(0,row['max_jobs']-totals['jobs']),
            'active_jobs':totals['jobs'],'reserved_memory_mb':totals['memory'],
            'reserved_vram_mb':totals['vram'],'reserved_cpu_slots':totals['cpu']}

def select_job(db,app,node,metrics,now):
    if not isinstance(node,str) or not NODE_ID.fullmatch(node):
        raise PermissionError('Worker token must be bound to an approved node.')
    pool=snapshot(db,node,now)
    rows=db.execute('SELECT j.id,j.task,j.payload,j.attempts,'
                    'r.memory_mb,r.vram_mb,r.cpu_slots '
                    'FROM jobs j LEFT JOIN task_resources r ON r.app=j.app AND r.task=j.task '
                    'WHERE j.app=? AND j.status="queued" ORDER BY j.created,j.id LIMIT 100',
                    (app,)).fetchall()
    if not rows:return None,'empty',pool
    active=db.execute('SELECT COUNT(*) FROM jobs WHERE app=? AND status="leased" '
                      'AND lease_until>?',(app,now)).fetchone()[0]
    max_parallel=db.execute('SELECT max_jobs FROM app_parallel_limits WHERE app=?',(app,)).fetchone()
    if active>=(max_parallel['max_jobs'] if max_parallel else 2):
        return None,'app_concurrency_limit',pool
    if not pool['available_job_slots']:
        return None,'node_concurrency_limit',pool
    # Fail closed rather than using an unobserved or non-NVIDIA GPU.
    obs=validate_telemetry(metrics,needs_gpu=pool['vram_mb']>0)
    for job in rows:
        if job['memory_mb'] is None:
            continue
        if (job['memory_mb']>pool['available_memory_mb'] or
            job['vram_mb']>pool['available_vram_mb'] or
            job['cpu_slots']>pool['available_cpu_slots']):
            continue
        # Subtract OS buffers from instantaneous free measurements too. This is
        # conservative: live free memory already accounts for running jobs.
        if (job['memory_mb']>max(0,obs['memory_available_mb']-pool['reserve_memory_mb']) or
            job['vram_mb']>max(0,obs['vram_available_mb']-pool['reserve_vram_mb'])):
            continue
        return job,None,pool
    return None,'resource_unavailable_or_profile_missing',pool
