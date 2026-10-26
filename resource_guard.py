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

from machine_policy import admission, sample as machine_sample
from native_runner_observer import runner_reservations

DEFAULT_MAX_PARALLEL=4
DEFAULT_MAX_PER_APP=2
DEFAULT_RAM_MB=512
DEFAULT_MIN_FREE_RAM_MB=0
INTEL_SHARED_GPU_MIN_FREE_RAM_MB=4096
INTEL_DESKTOP_MAX_PARALLEL=3
MAX_INTEGRATED_GPU_JOBS=1
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

def physical_ram_total_mb():
    try:
        data=Path('/proc/meminfo').read_text(encoding='ascii')
        match=re.search(r'^MemTotal:\s+(\d+)\s+kB$',data,re.M)
        return int(match.group(1))//1024 if match else None
    except (OSError,ValueError):
        return None

def intel_iris_xe_shared_gpu():
    """Detect known Intel Tiger Lake Iris Xe iGPUs without inventing dedicated VRAM."""
    tiger_lake_ids={0x9A40,0x9A49,0x9A59,0x9A60,0x9A68,0x9A70,0x9A78}
    try:
        for card in sorted(Path('/sys/class/drm').glob('card[0-9]*')):
            device=card/'device'
            vendor=int((device/'vendor').read_text(encoding='ascii').strip(),16)
            ident=int((device/'device').read_text(encoding='ascii').strip(),16)
            if vendor==0x8086 and ident in tiger_lake_ids:
                return {0:{'mode':'shared','vendor':'intel',
                    'model':'Intel Iris Xe integrated (Tiger Lake)',
                    'total_mb':None,'free_mb':None,
                    'note':'No dedicated VRAM pool. GPU allocations share system RAM.'}}
    except (OSError,ValueError):
        return {}
    return {}

def gpu_memory_inventory():
    """Expose dedicated NVIDIA telemetry, or known Intel shared-memory graphics."""
    dedicated=nvidia_vram_mb()
    if dedicated:
        return {idx:{**info,'mode':'dedicated','vendor':'nvidia'}
                for idx,info in dedicated.items()}
    return intel_iris_xe_shared_gpu()

class ResourceGate:
    def __init__(self,memory_reader=memory_available_mb,gpu_reader=gpu_memory_inventory,
                 policy_reader=None,runner_reader=None):
        self.memory_reader=memory_reader
        self.gpu_reader=gpu_reader
        # Fake sensor-based tests must not depend on unrelated CI host pressure.
        self.policy_reader=(policy_reader if policy_reader is not None else
                            machine_sample if memory_reader is memory_available_mb and
                            gpu_reader is gpu_memory_inventory else lambda:{})
        self.runner_reader=(runner_reader if runner_reader is not None else
                            runner_reservations if memory_reader is memory_available_mb and
                            gpu_reader is gpu_memory_inventory else lambda:{
                                'reserved_ram_mb':0,'active_runners':0,'reserved_cpus':0})

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
            CREATE TABLE IF NOT EXISTS runtime_controls(
                id INTEGER PRIMARY KEY CHECK(id=1),
                mode TEXT NOT NULL CHECK(mode IN ('quiet','balanced','performance')),
                paused INTEGER NOT NULL CHECK(paused IN (0,1))
            );
            INSERT OR IGNORE INTO runtime_controls(id,mode,paused)
                VALUES(1,'balanced',0);
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
            or type(min_free_ram_mb)!=int or not 0<=min_free_ram_mb<=MAX_MEMORY_MB
            or type(min_free_vram_mb)!=int or not 0<=min_free_vram_mb<=MAX_MEMORY_MB):
            raise ValueError('Invalid global parallel/RAM/VRAM reserve policy.')
        for key,value in [('max_parallel',max_parallel),
                          ('min_free_ram_mb',min_free_ram_mb),
                          ('min_free_vram_mb',min_free_vram_mb)]:
            db.execute('INSERT INTO resource_config(name,value) VALUES(?,?) '
                       'ON CONFLICT(name) DO UPDATE SET value=excluded.value',
                       (key,value))

    @staticmethod
    def controls(db):
        row=db.execute('SELECT mode,paused FROM runtime_controls WHERE id=1').fetchone()
        return {'mode':row['mode'],'paused':bool(row['paused'])}

    @staticmethod
    def set_controls(db,mode=None,paused=None):
        if mode is not None and mode not in ('quiet','balanced','performance'):
            raise ValueError('Invalid operating mode.')
        if paused is not None and type(paused) is not bool:
            raise ValueError('paused must be boolean.')
        current=ResourceGate.controls(db)
        next_mode=mode if mode is not None else current['mode']
        next_paused=paused if paused is not None else current['paused']
        db.execute('UPDATE runtime_controls SET mode=?,paused=? WHERE id=1',
                   (next_mode,int(next_paused)))
        return {'mode':next_mode,'paused':next_paused}

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
        return db.execute('SELECT app,task FROM jobs WHERE status IN ("leased","stranded")').fetchall()

    def snapshot(self,db):
        gpu=self.gpu_reader()
        cfg=self.config(db)
        intel=any(isinstance(info,dict) and info.get('mode')=='shared'
                  and info.get('vendor')=='intel' for info in gpu.values())
        total=physical_ram_total_mb() if intel else None
        if intel and total is not None and total<=24576:
            configured={row['name'] for row in db.execute('SELECT name FROM resource_config')}
            if 'max_parallel' not in configured:
                cfg['max_parallel']=INTEL_DESKTOP_MAX_PARALLEL
        active=self._active(db)
        native=self.runner_reader()
        controls=self.controls(db)
        observed=self.policy_reader()
        policy=admission(controls['mode'],observed,cfg['max_parallel'])
        if native is None:
            native={'reserved_ram_mb':None,'active_runners':None,'reserved_cpus':None}
        used_ram=0
        shared_ram=0
        reserved_vram={}
        used_apps={}
        active_integrated=0
        for row in active:
            profile=self.profile(db,row['app'],row['task'])
            used_ram+=profile['ram_mb']
            if profile['vram_mb']:
                idx=profile['gpu_index']
                info=gpu.get(idx)
                if info is not None and info.get('mode','dedicated')=='dedicated':
                    reserved_vram[idx]=reserved_vram.get(idx,0)+profile['vram_mb']
                else:
                    # A vanished GPU sensor must never release an in-use RAM budget.
                    used_ram+=profile['vram_mb']
                    shared_ram+=profile['vram_mb']
                    active_integrated+=1
            used_apps[row['app']]=used_apps.get(row['app'],0)+1
        return {'capacity':cfg,'memory_available_mb':self.memory_reader(),
                'reserved_ram_mb':used_ram,'reserved_native_runner_ram_mb':native['reserved_ram_mb'],
                'native_runners':native['active_runners'],'native_cpu_slots':native['reserved_cpus'],
                'policy':{**policy,'mode':controls['mode'],'paused':controls['paused']},
                'reserved_shared_gpu_ram_mb':shared_ram,
                'gpu':gpu,'reserved_vram_mb':reserved_vram,
                'active_integrated_gpu_jobs':active_integrated,
                'max_integrated_gpu_jobs':MAX_INTEGRATED_GPU_JOBS,
                'active_jobs':len(active),'active_by_app':used_apps,
                'trust':'admission-only; host workers must enforce memory limits'}

    def admit(self,db,app,task,snapshot=None):
        """Only call inside a SQLite BEGIN IMMEDIATE transaction."""
        s=snapshot or self.snapshot(db)
        profile=self.profile(db,app,task)
        cfg=s['capacity']
        if s['policy']['paused']:
            return None,'admissions_paused'
        if not s['policy']['max_parallel']:
            return None,s['policy']['reason']
        if s['active_jobs']>=s['policy']['max_parallel']:
            return None,'global_parallel_limit'
        if s['active_by_app'].get(app,0)>=self.app_limit(db,app):
            return None,'project_parallel_limit'
        available=s['memory_available_mb']
        if available is None:
            return None,'ram_sensor_unavailable'
        native_ram=s['reserved_native_runner_ram_mb']
        if native_ram is None:
            return None,'native_runner_telemetry_unavailable'
        # Subtract reservations even though some may already be physically used:
        # deliberately conservative rather than double-spending system headroom.
        reading=s['gpu'].get(profile['gpu_index']) if profile['vram_mb'] else None
        mode=reading.get('mode','dedicated') if reading else None
        required_ram=profile['ram_mb']
        if profile['vram_mb'] and mode=='shared':
            # Integrated GPU allocations draw from the SAME physical RAM.
            required_ram+=profile['vram_mb']
        # CPU/mobile jobs no longer inherit a blanket free-RAM reserve. Their
        # declared task budget must still fit in unreserved available memory.
        # Intel shared-memory GPU jobs retain a 4 GiB floor because GPU memory
        # is carved from the same physical RAM as the OS and other processes.
        minimum_ram=cfg['min_free_ram_mb']
        if profile['vram_mb'] and mode=='shared':
            minimum_ram=max(minimum_ram,INTEL_SHARED_GPU_MIN_FREE_RAM_MB)
        if available-s['reserved_ram_mb']-native_ram-minimum_ram<required_ram:
            return None,'ram_headroom'
        if profile['vram_mb']:
            if not reading:
                return None,'gpu_sensor_unavailable'
            if mode=='shared':
                if s['active_integrated_gpu_jobs']>=MAX_INTEGRATED_GPU_JOBS:
                    return None,'integrated_gpu_busy'
            elif mode=='dedicated':
                if (reading['free_mb']-s['reserved_vram_mb'].get(profile['gpu_index'],0)
                    -cfg['min_free_vram_mb']<profile['vram_mb']):
                    return None,'vram_headroom'
            else:
                return None,'gpu_sensor_unavailable'
        return {**profile,'gpu_memory_mode':mode,
                'effective_ram_mb':required_ram,
                'min_free_ram_mb':minimum_ram},None
