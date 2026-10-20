"""Resource-aware admission for concurrent app workers.

This is a coordination gate, NOT a RAM/VRAM hard limit. Hard RAM enforcement
requires a separately configured cgroup/systemd memory policy on each worker.
GPU VRAM is generally not a portable hard-limited resource; use live telemetry,
reserved headroom, and isolate competing GPU processes.
"""
from __future__ import annotations
import os
from pathlib import Path
import re
import subprocess

DEFAULT_MAX_PARALLEL=4
DEFAULT_MAX_PER_APP=2
DEFAULT_RAM_MB=512
DEFAULT_MIN_FREE_RAM_MB=2048
DEFAULT_MIN_FREE_VRAM_MB=1024
MAX_MEMORY_MB=262144
MAX_GPU_INDEX=15
MB=1024*1024

def memory_available_mb():
    """Use the tighter of Linux MemAvailable and remaining cgroup-v2 capacity."""
    try:
        values={}
        for line in Path('/proc/meminfo').read_text(encoding='ascii').splitlines():
            if ':' in line:
                key,value=line.split(':',1)
                values[key]=value.strip()
        available=values.get('MemAvailable','')
        match=re.fullmatch(r'(\d+)\s+kB',available)
        if not match:
            return None
        amount=int(match.group(1))*1024
        cg=Path('/sys/fs/cgroup')
        maximum=(cg/'memory.max').read_text(encoding='ascii').strip()
        current=int((cg/'memory.current').read_text(encoding='ascii').strip())
        if maximum!='max':
            amount=min(amount,max(0,int(maximum)-current))
        return amount//MB
    except (OSError,ValueError,OverflowError):
        # In non-cgroup-v2 environments, still use MemAvailable when available.
        try:
            match=re.search(r'^MemAvailable:\s+(\d+)\s+kB$',Path('/proc/meminfo').read_text(),re.M)
            return int(match.group(1))//1024 if match else None
        except (OSError,ValueError):
            return None

def nvidia_vram_mb():
    """GPU IDs map to nvidia-smi GPU indices. Unknown vendor => unavailable."""
    if os.name!='posix':
        return {}
    try:
        result=subprocess.run(['nvidia-smi','--query-gpu=index,memory.total,memory.free',
            '--format=csv,noheader,nounits'],check=False,timeout=3,
            text=True,encoding='utf-8',errors='replace',
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        if result.returncode:
            return {}
        found={}
        for line in result.stdout.splitlines():
            values=[x.strip() for x in line.split(',')]
            if len(values)!=3 or not all(x.isdecimal() for x in values):
                return {}
            idx,total,free=map(int,values)
            if idx>MAX_GPU_INDEX or idx in found or not 0<=free<=total<=MAX_MEMORY_MB:
                return {}
            found[idx]={'total_mb':total,'free_mb':free}
        return found
    except (OSError,subprocess.TimeoutExpired,ValueError):
        return {}

class ResourceGate:
    def __init__(self, memory_reader=memory_available_mb, gpu_reader=nvidia_vram_mb):
        self.memory_reader=memory_reader
        self.gpu_reader=gpu_reader

    @staticmethod
    def setup(db):
        db.executescript('''
            CREATE TABLE IF NOT EXISTS resource_config (
                name TEXT PRIMARY KEY, value INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS project_limits (
                app TEXT PRIMARY KEY, parallel INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS resource_profiles (
                app TEXT NOT NULL, task TEXT NOT NULL,
                ram_mb INTEGER NOT NULL, vram_mb INTEGER NOT NULL,
                gpu_index INTEGER,
                PRIMARY KEY(app,task)
            );
        ''')

    @staticmethod
    def config(db):
        values={r['name']:r['value'] for r in db.execute(
            'SELECT name,value FROM resource_config')}
        return {'max_parallel':values.get('max_parallel',DEFAULT_MAX_PARALLEL),
                'min_free_ram_mb':values.get('min_free_ram_mb',DEFAULT_MIN_FREE_RAM_MB),
                'min_free_vram_mb':values.get('min_free_vram_mb',DEFAULT_MIN_FREE_VRAM_MB)}

    @staticmethod
    def set_config(db,*,max_parallel,min_free_ram_mb,min_free_vram_mb):
        if (type(max_parallel)!=int or not 1<=max_parallel<=32
            or type(min_free_ram_mb)!=int or not 256<=min_free_ram_mb<=MAX_MEMORY_MB
            or type(min_free_vram_mb)!=int or not 0<=min_free_vram_mb<=MAX_MEMORY_MB):
            raise ValueError('Invalid global parallel/RAM/VRAM reserve policy.')
        for key,value in [('max_parallel',max_parallel),
                          ('min_free_ram_mb',min_free_ram_mb),
                          ('min_free_vram_mb',min_free_vram_mb)]:
            db.execute('INSERT INTO resource_config(name,value) VALUES(?,?) '
                       'ON CONFLICT(name) DO UPDATE SET value=excluded.value',
                       (key,value))

    @staticmethod
    def profile(db,app,task):
        row=db.execute('SELECT ram_mb,vram_mb,gpu_index FROM resource_profiles '
                       'WHERE app=? AND task=?',(app,task)).fetchone()
        if not row:return {'ram_mb':DEFAULT_RAM_MB,'vram_mb':0,'gpu_index':None}
        return {'ram_mb':row['ram_mb'],'vram_mb':row['vram_mb'],
                'gpu_index':row['gpu_index']}

    @staticmethod
    def validate_profile(ram_mb,vram_mb,gpu_index):
        if (type(ram_mb)!=int or not 64<=ram_mb<=MAX_MEMORY_MB
            or type(vram_mb)!=int or not 0<=vram_mb<=MAX_MEMORY_MB
            or (gpu_index is not None and (type(gpu_index)!=int
                 or not 0<=gpu_index<=MAX_GPU_INDEX))
            or (vram_mb>0) != (gpu_index is not None)):
            raise ValueError('Invalid task budget: RAM 64-262144 MiB, and VRAM requires one GPU index.')

    @staticmethod
    def set_profile(db,app,task,ram_mb,vram_mb,gpu_index):
        ResourceGate.validate_profile(ram_mb,vram_mb,gpu_index)
        db.execute('INSERT INTO resource_profiles(app,task,ram_mb,vram_mb,gpu_index) '
                   'VALUES(?,?,?,?,?) ON CONFLICT(app,task) DO UPDATE SET '
                   'ram_mb=excluded.ram_mb,vram_mb=excluded.vram_mb,'
                   'gpu_index=excluded.gpu_index',
                   (app,task,ram_mb,vram_mb,gpu_index))

    @staticmethod
    def app_limit(db,app):
        row=db.execute('SELECT parallel FROM project_limits WHERE app=?',(app,)).fetchone()
        return row['parallel'] if row else DEFAULT_MAX_PER_APP

    @staticmethod
    def set_app_limit(db,app,parallel):
        if type(parallel)!=int or not 1<=parallel<=32:
            raise ValueError('Application parallelism limit must be 1-32.')
        db.execute('INSERT INTO project_limits(app,parallel) VALUES(?,?) '
                   'ON CONFLICT(app) DO UPDATE SET parallel=excluded.parallel',
                   (app,parallel))

    @staticmethod
    def _active(db):
        return db.execute('SELECT app,task FROM jobs WHERE status="leased" '
                          'AND lease_until>strftime("%s","now")').fetchall()

    def snapshot(self,db):
        cfg=self.config(db)
        live_ram=self.memory_reader()
        gpu=self.gpu_reader()
        active=self._active(db)
        used_ram=0
        reserved_vram={}
        used_apps={}
        for row in active:
            p=self.profile(db,row['app'],row['task'])
            used_ram+=p['ram_mb']
            if p['vram_mb']:
                idx=p['gpu_index']
                reserved_vram[idx]=reserved_vram.get(idx,0)+p['vram_mb']
            used_apps[row['app']]=used_apps.get(row['app'],0)+1
        return {'capacity':cfg,'memory_available_mb':live_ram,
                'reserved_ram_mb':used_ram,
                'gpu':gpu,'reserved_vram_mb':reserved_vram,
                'active_jobs':len(active),'active_by_app':used_apps,
                'trust':'admission-only; external workers must enforce memory limits'}

    def admit(self,db,app,task,snapshot=None):
        """Only call inside a SQLite BEGIN IMMEDIATE transaction."""
        s=snapshot or self.snapshot(db)
        profile=self.profile(db,app,task)
        cfg=s['capacity']
        if s['active_jobs']>=cfg['max_parallel']:
            return None,'global_parallel_limit'
        if s['active_by_app'].get(app,0)>=self.app_limit(db,app):
            return None,'project_parallel_limit'
        available=s['memory_available_mb']
        if available is None:
            return None,'ram_sensor_unavailable'
        # Subtract reservations even though some may already be physically used:
        # deliberately conservative rather than double-spending system headroom.
        if available-s['reserved_ram_mb']-cfg['min_free_ram_mb']<profile['ram_mb']:
            return None,'ram_headroom'
        if profile['vram_mb']:
            idx=profile['gpu_index']
            reading=s['gpu'].get(idx)
            if reading is None:
                return None,'gpu_sensor_unavailable'
            if (reading['free_mb']-s['reserved_vram_mb'].get(idx,0)
                -cfg['min_free_vram_mb']<profile['vram_mb']):
                return None,'vram_headroom'
        return profile,None
