"""Observe real native CI jobs and managed runner containers for RAM admission.

Docker-limited runner reservations are not enough: host-native GitHub Actions
Runner.Worker and Open CI Hub's standalone act executor run directly on the PC.
Count those processes explicitly before admitting *another* build. This is a
conservative reservation, not a cgroup memory limit on existing native jobs.
An unreadable process table fails closed so capacity cannot be double-spent.
"""
from __future__ import annotations
import re
import shutil
import subprocess

MANAGED=re.compile(r'^(?:open-ci-(?:github|gitlab|circleci)-runner|open-ci-hub-pool-[a-f0-9]{16})$')
DEFAULT_CPU_LIMIT=1.5

# Conservative per-active-job headroom on a 16-GiB desktop. Idle listeners do
# not count. These are admission reservations, not measured RSS or enforced caps.
HOST_JOB_RAM_MB={'Runner.Worker':6144,'act':4096}
HOST_JOB_CPU_SLOTS={'Runner.Worker':2.0,'act':1.5}
MAX_HOST_JOBS=16

def host_ci_reservations():
    """Detect host-native CI work by exact process comm, not command-line text.

    An idle Runner.Listener is deliberately excluded. Never trust a failed or
    truncated process enumeration to mean that no CI is running.
    """
    if not shutil.which('ps'):
        return None
    try:
        result=subprocess.run(['ps','-eo','comm=,args='],
            capture_output=True,text=True,timeout=3,check=False)
        if result.returncode!=0 or len(result.stdout)>2_000_000:
            return None
        ram=count=0
        cpu=0.0
        for line in result.stdout.splitlines():
            parts=line.split(None,1)
            if not parts:continue
            name=parts[0]
            if name not in HOST_JOB_RAM_MB:continue
            count+=1
            if count>MAX_HOST_JOBS:return None
            ram+=HOST_JOB_RAM_MB[name]
            cpu+=HOST_JOB_CPU_SLOTS[name]
        return {'reserved_ram_mb':ram,'active_runners':count,'reserved_cpus':round(cpu,2)}
    except (OSError,subprocess.TimeoutExpired,ValueError):
        return None


def docker_runner_idle(name):
    """Conservatively detect a *listener only*, never discount ambiguous jobs.

    Idle dockers have memory limits, but their process RSS is already captured
    by the host memory available reading. Reserve the full Docker cap for any
    active or unknown process, job container, or failed telemetry.
    """
    profiles = {
        "open-ci-github-runner": (
            {"run.sh", "run-helper.sh", "Runner.Listener"}, "Runner.Listener"),
        "open-ci-gitlab-runner": (
            {"dumb-init", "gitlab-runner"}, "gitlab-runner"),
    }
    if name not in profiles:
        return False
    allowed, required = profiles[name]
    try:
        result = subprocess.run(
            ['docker', 'top', name, '-eo', 'pid,comm,args'],
            capture_output=True, text=True, timeout=3, check=False)
        if result.returncode or not result.stdout or len(result.stdout) > 16384:
            return False
        rows = result.stdout.splitlines()
        if not rows or not rows[0].startswith('PID') or not 1 < len(rows) <= 20:
            return False
        names = []
        for line in rows[1:]:
            parts = line.split(None, 2)
            if len(parts) != 3 or not parts[0].isdecimal():
                return False
            names.append(parts[1])
        return required in names and set(names).issubset(allowed)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False


def runner_reservations():
    if not shutil.which('docker'):
        # No Docker CLI means provider usage cannot be authoritatively inspected.
        return None
    try:
        ps=subprocess.run(['docker','ps','--format','{{.Names}}'],
            capture_output=True,text=True,timeout=3,check=False)
        if ps.returncode!=0:return None
        running_names=[x.strip() for x in ps.stdout.splitlines() if x.strip()]
        names=[x for x in running_names if MANAGED.fullmatch(x)]
        # A GitLab executor may run jobs in separate runner-* containers.
        # If any are present, do not call its controller an idle listener.
        unknown_ci_jobs=any(x.startswith('runner-') for x in running_names)
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
                active=unknown_ci_jobs or not docker_runner_idle(name)
                if active:
                    total+=(memory+1024*1024-1)//(1024*1024)
                    cpu+=nano/1e9 if nano else DEFAULT_CPU_LIMIT
                    count+=1
        host=host_ci_reservations()
        if host is None:return None
        return {'reserved_ram_mb':total+host['reserved_ram_mb'],
                'active_runners':count+host['active_runners'],
                'reserved_cpus':round(cpu+host['reserved_cpus'],2),
                'trust':'Active Docker runner reservations plus conservative native Runner.Worker and act reservations; idle listeners verified by process inventory'}
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
