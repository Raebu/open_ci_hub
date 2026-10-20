"""Conservative local Linux resource sampling for Open CI Hub workers.

NVIDIA single-GPU preflight only. Unknown VRAM is reported as 0, never guessed.
Observations are a safeguard, not a hard RAM/VRAM quota or attestation.
"""
from __future__ import annotations
import math
from pathlib import Path
import subprocess
import time

MIB=1024*1024

def _host_memory_mb():
    raw=Path('/proc/meminfo').read_text(encoding='utf-8')
    for line in raw.splitlines():
        if line.startswith('MemAvailable:'):
            return max(0,int(line.split()[1])//1024)
    raise RuntimeError('Linux MemAvailable telemetry unavailable.')

def _cgroup_headroom_mb():
    # A container's /proc/meminfo can report HOST memory instead of its cgroup.
    for base in (Path('/sys/fs/cgroup'),):
        maximum=base/'memory.max';current=base/'memory.current'
        if not maximum.exists() or not current.exists():continue
        a=maximum.read_text().strip()
        if a=='max':return None
        limit=int(a);used=int(current.read_text().strip())
        return max(0,(limit-used)//MIB)
    return None

def _nvidia_free_mb(index=0):
    if type(index) is not int or index<0 or index>15:
        raise ValueError('Invalid GPU selection.')
    try:
        result=subprocess.run(['nvidia-smi','--id='+str(index),
            '--query-gpu=memory.free','--format=csv,noheader,nounits'],
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            text=True,timeout=3,check=False)
    except (OSError,subprocess.TimeoutExpired):
        return 0
    if result.returncode:return 0
    try:
        lines=result.stdout.strip().splitlines()
        if len(lines)!=1:return 0
        return max(0,int(lines[0].strip()))
    except ValueError:return 0

def sample(*,gpu_index=0):
    physical=_host_memory_mb()
    cgroup=_cgroup_headroom_mb()
    available=min(physical,cgroup) if cgroup is not None else physical
    return {'memory_available_mb':available,
            'vram_available_mb':_nvidia_free_mb(gpu_index),
            'observed_at':time.time(),
            'source':'linux-worker-preflight'}

if __name__=='__main__':
    import json
    print(json.dumps(sample(),indent=2))
