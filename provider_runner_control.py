#!/usr/bin/env python3
"""Opt-in, non-destructive admission gate for existing provider Compose runners.

No Docker container creation, deletion, image pulling or volume modification.
Only an explicitly host-approved, already-existing bounded runner may be started.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from hub import secure_write
from state_lock import setup_lock
from native_runner_observer import require_start_capacity

ROOT=Path(__file__).resolve().parent
APPROVAL=ROOT/'.local'/'provider-runner-approvals.json'
PROFILES={
    'github': 'open-ci-github-runner',
    'gitlab': 'open-ci-gitlab-runner',
    'circleci': 'open-ci-circleci-runner',
}
SERVICE={key:key for key in PROFILES}
MAX_MEMORY=2*1024**3
MAX_CPUS=1_500_000_000
MAX_PIDS=256

def docker(args):
    try:
        done=subprocess.run(['docker',*args],capture_output=True,
             text=True,timeout=15,check=False)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise RuntimeError('Docker unavailable or timed out; no action taken.') from None
    if done.returncode:
        raise RuntimeError('Docker inspect/start failed; inspect host locally. No deletion attempted.')
    if len(done.stdout)>32768:
        raise RuntimeError('Docker inspection output unexpectedly large.')
    return done.stdout.strip()

def allowlist():
    if APPROVAL.is_symlink() or APPROVAL.parent.is_symlink():
        raise RuntimeError('Refusing symlinked provider runner approvals.')
    if not APPROVAL.exists():
        return {'approved':[]}
    try:
        d=json.loads(APPROVAL.read_text(encoding='utf-8'))
    except (OSError,ValueError):
        raise RuntimeError('Corrupt provider runner approvals; recover manually.') from None
    if not isinstance(d,dict) or not isinstance(d.get('approved'),list):
        raise RuntimeError('Invalid provider runner approval file.')
    names=d['approved']
    if any(type(n)!=str or n not in PROFILES for n in names) or len(names)!=len(set(names)):
        raise RuntimeError('Unexpected provider runner approval ID.')
    return d

def approve(provider,*,confirm=False):
    if provider not in PROFILES:raise ValueError('Unknown runner provider.')
    if confirm is not True:
        raise PermissionError('Host owner must explicitly approve provider runner starts.')
    data=allowlist()
    if provider not in data['approved']:
        data['approved'].append(provider)
        secure_write(APPROVAL,json.dumps(data,indent=2)+'\n')
    return {'approved':data['approved'],'note':'Approval does not start or change containers.'}

def inspect(provider):
    if provider not in PROFILES:raise ValueError('Unknown runner provider.')
    name=PROFILES[provider]
    # Read only nonsecret HostConfig and Docker Compose project/service labels.
    raw=docker(['inspect','--format','{{json .HostConfig}}',name])
    try:
        host=json.loads(raw)
    except (ValueError,TypeError):
        raise RuntimeError('Invalid Docker HostConfig; refusing runner start.') from None
    if not isinstance(host,dict):
        raise RuntimeError('Invalid Docker HostConfig; refusing runner start.')
    try:
        labels=json.loads(docker(['inspect','--format','{{json .Config.Labels}}',name]))
    except (ValueError,TypeError):
        raise RuntimeError('Runner labels unreadable; refusing start.') from None
    if not isinstance(labels,dict):
        raise RuntimeError('Runner labels missing; refusing start.')
    if (labels.get('com.docker.compose.project')!='open-ci-hub' or
        labels.get('com.docker.compose.service')!=SERVICE[provider]):
        raise RuntimeError('Docker container does not match approved Open CI Hub Compose identity.')
    memory=host.get('Memory')
    swap=host.get('MemorySwap')
    cpu=host.get('NanoCpus')
    pids=host.get('PidsLimit')
    security=host.get('SecurityOpt')
    capdrop=host.get('CapDrop')
    restart=host.get('RestartPolicy')
    if not (
        type(memory)==int and 0<memory<=MAX_MEMORY and
        type(swap)==int and swap==memory and
        type(cpu)==int and 0<cpu<=MAX_CPUS and
        type(pids)==int and 0<pids<=MAX_PIDS and
        isinstance(capdrop,list) and 'ALL' in capdrop and
        isinstance(security,list) and
        any(isinstance(opt,str) and opt.lower()=='no-new-privileges:true'
            for opt in security) and
        isinstance(restart,dict) and restart.get('Name') in ('','no') and
        host.get('Privileged') is False and
        not host.get('NetworkMode')=='host'
    ):
        raise RuntimeError('Runner lacks verified resource/security caps or uses auto-restart; '
                           'preserve it and inspect locally before migration.')
    state=docker(['inspect','--format','{{.State.Status}}',name])
    if state not in ('created','exited','running'):
        raise RuntimeError('Runner state is not startable without intervention.')
    return {'provider':provider,'container':name,'state':state,'memory_mb':memory//(1024*1024),
            'cpu_cores':cpu/1e9,'pids_limit':pids,'bounded':True}

def start(provider):
    if provider not in allowlist()['approved']:
        raise PermissionError('Runner provider not approved on this host.')
    data=inspect(provider)
    if data['state']=='running':
        return {'status':'running','provider':provider,'note':'Existing running jobs were left untouched.'}
    # Reuse the same broker and disk/thermal/CPU/memory pressure gate as app jobs.
    require_start_capacity(data['memory_mb'])
    name=PROFILES[provider]
    docker(['start',name])
    return {'status':'started','provider':provider,'container':name}

def status():
    approved=allowlist()['approved']
    result=[]
    for provider in PROFILES:
        try:
            result.append({**inspect(provider),'approved':provider in approved})
        except RuntimeError as e:
            result.append({'provider':provider,'approved':provider in approved,
                           'healthy':False,'reason':str(e)[:180]})
    return {'runners':result,'note':'Read-only inspection; no container changes.'}

def schedule():
    values={}
    for provider in allowlist()['approved']:
        try:
            values[provider]=start(provider)
        except (RuntimeError,PermissionError,ValueError) as exc:
            values[provider]={'status':'deferred','reason':str(exc)[:180]}
    return {'providers':values,'warning':'Scheduler never stops running provider jobs or creates containers.'}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    auth=sub.add_parser('approve')
    auth.add_argument('provider',choices=sorted(PROFILES))
    auth.add_argument('--confirm',action='store_true')
    start_p=sub.add_parser('start');start_p.add_argument('provider',choices=sorted(PROFILES))
    sub.add_parser('schedule')
    sub.add_parser('status')
    args=parser.parse_args(argv)
    if os.name!='posix' or os.geteuid()==0:
        print('Run as a non-root Linux desktop owner, not as root.',file=sys.stderr)
        return 2
    try:
        # One lock shared with the existing approved per-repo runner pool.
        with setup_lock(ROOT/'.local'):
            if args.command=='approve':value=approve(args.provider,confirm=args.confirm)
            elif args.command=='start':value=start(args.provider)
            elif args.command=='schedule':value=schedule()
            else:value=status()
        print(json.dumps(value,indent=2))
        return 0
    except (RuntimeError,PermissionError,ValueError,OSError) as exc:
        print('Provider runner guard:',str(exc)[:220],file=sys.stderr)
        return 1

if __name__=='__main__':
    raise SystemExit(main())
