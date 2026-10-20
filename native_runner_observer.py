"""Observe running native CI Docker containers for shared app resource accounting.

Existing Github/GitLab/CircleCI and per-repo pool containers have Docker hard
memory limits. The app broker must subtract those reservations when deciding
whether additional app tasks can start. This does not constitute a single
enforcing scheduler across provider work already running.
"""
from __future__ import annotations
import re
import shutil
import subprocess

MANAGED=re.compile(r'^(?:open-ci-(?:github|gitlab|circleci)-runner|open-ci-hub-pool-[a-f0-9]{16})$')
DEFAULT_CPU_LIMIT=1.5

def runner_reservations():
    if not shutil.which('docker'):
        # No Docker CLI means provider usage cannot be authoritatively inspected.
        return None
    try:
        ps=subprocess.run(['docker','ps','--format','{{.Names}}'],
            capture_output=True,text=True,timeout=3,check=False)
        if ps.returncode!=0:return None
        names=[x.strip() for x in ps.stdout.splitlines() if MANAGED.fullmatch(x.strip())]
        if len(names)>128:return None
        total=0;count=0;cpu=0.0
        if names:
            # One bounded inspect for the entire runner fleet; never hold the
            # broker's SQLite lock during O(N) sequential Docker subprocesses.
            p=subprocess.run(['docker','inspect','--format',
                '{{.Name}} {{.HostConfig.Memory}} {{.HostConfig.NanoCpus}}',*names],
                capture_output=True,text=True,timeout=5,check=False)
            if p.returncode:return None
            entries=p.stdout.splitlines()
            if len(entries)!=len(names):return None
            seen=set()
            for line in entries:
                parts=line.strip().split()
                if len(parts)!=3 or not all(x.isdecimal() for x in parts[1:]):
                    return None
                name=parts[0].lstrip('/')
                if name not in names or name in seen:return None
                seen.add(name)
                memory,nano=map(int,parts[1:])
                if memory<=0 or memory>512*1024**3:return None
                total+=(memory+1024*1024-1)//(1024*1024)
                cpu+=nano/1e9 if nano else DEFAULT_CPU_LIMIT
                count+=1
        return {'reserved_ram_mb':total,'active_runners':count,'reserved_cpus':round(cpu,2),
                'trust':'Docker hard memory limits; jobs can also consume host storage/I/O'}
    except (OSError,subprocess.TimeoutExpired,ValueError):
        return None


def require_start_capacity(reserve_mb=2048):
    """Protect active app memory before a new native CI container is launched.

    Called only on the real PC, not in a GitHub Actions job. Docker-specific
    hard limits continue to protect individual runner containers.
    """
    if type(reserve_mb)!=int or reserve_mb<512:
        raise ValueError('Invalid runner memory reservation.')
    from app_runtime import Broker
    from resource_guard import memory_available_mb
    state=Broker().resource_status()
    p=state['policy']
    if p['paused'] or p['max_parallel']==0:
        raise RuntimeError('Native runner start blocked by pause or critical PC pressure.')
    avail=memory_available_mb()
    existing=state['reserved_native_runner_ram_mb']
    if avail is None or existing is None:
        raise RuntimeError('Cannot safely measure CI runner/RAM reservations.')
    left=avail-state['reserved_ram_mb']-existing-state['capacity']['min_free_ram_mb']
    if left<reserve_mb:
        raise RuntimeError('CI runner start deferred to preserve OS/desktop RAM and app jobs.')
    return {'admitted':True,'reserved_mb':reserve_mb,'estimated_free_after_mb':left-reserve_mb}
