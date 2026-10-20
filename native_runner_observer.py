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
        for name in names:
            p=subprocess.run(['docker','inspect','--format',
                '{{.HostConfig.Memory}} {{.HostConfig.NanoCpus}}',name],
                capture_output=True,text=True,timeout=3,check=False)
            if p.returncode:return None
            parts=p.stdout.strip().split()
            if len(parts)!=2 or not all(x.isdecimal() for x in parts):
                return None
            memory,nano=map(int,parts)
            if memory<=0 or memory>512*1024**3:return None
            total+=(memory+1024*1024-1)//(1024*1024)
            cpu+=nano/1e9 if nano else DEFAULT_CPU_LIMIT
            count+=1
        return {'reserved_ram_mb':total,'active_runners':count,'reserved_cpus':round(cpu,2),
                'trust':'Docker hard memory limits; jobs can also consume host storage/I/O'}
    except (OSError,subprocess.TimeoutExpired,ValueError):
        return None
