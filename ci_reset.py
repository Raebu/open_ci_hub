#!/usr/bin/env python3
"""Owner-authorised local CI reset. Preserve applications and two newest runs.

Stops/restarts only existing CI services. Never touches production app services,
provider historical records, runner registrations or credentials.
"""
from __future__ import annotations
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.request

import ci_bridge
from ci_job_control import cancel, setup
from hub import secure_write

ROOT=Path(__file__).resolve().parent
SERVICES=('open-ci-hub-bridge-poll.service','open-ci-hub-bridge-worker.service','custom-ci-api.service')
LEGACY=Path.home()/'custom-ci-api'
CONTAINER=re.compile(r'^open-ci-[a-f0-9]{20}-[0-9]+$')


def run(argv, timeout=60):
    return subprocess.run(argv,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=timeout,check=False)


def remove_private_tree(path):
    if path.is_symlink():
        raise PermissionError('Refusing symlinked CI cleanup path')
    if path.is_dir():shutil.rmtree(path)


def reset(keep=2):
    if keep!=2:
        raise ValueError('Owner policy requires keeping exactly two most recent local runs')
    folder=ROOT/'.local'/'ci-reset'
    if folder.is_symlink():raise PermissionError('Unsafe reset state')
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(folder/'lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        active=[]
        for unit in SERVICES:
            if run(['systemctl','--user','is-active','--quiet',unit]).returncode==0:active.append(unit)
        out={'started':time.time(),'keep':keep,'cancelled_hub':[],'legacy_active_before':None,
             'removed_logs':0,'removed_hub_records':0,'removed_workspaces':0,'retained':[],
             'services_restored':False,'scope':'local Hub and existing legacy CI only'}
        try:
            try:
                with urllib.request.urlopen('http://127.0.0.1:8090/queue',timeout=3) as response:
                    snapshot=json.loads(response.read(50000))
                out['legacy_active_before']=snapshot.get('active_or_queued')
            except (OSError,ValueError):pass
            # Stop admission before changing durable queue state. No app services are stopped.
            for unit in active:
                r=run(['systemctl','--user','stop',unit],timeout=60)
                if r.returncode:raise RuntimeError('Could not stop CI service '+unit)
            b=ci_bridge.get_broker();setup(b)
            rows=ci_bridge.status(b)
            with b._db() as db:
                rows=[dict(r) for r in db.execute('SELECT e.*,j.status AS scheduler_status FROM ci_bridge_events e JOIN jobs j ON j.id=e.job_id')]
            listing=run(['docker','ps','-a','--format','{{.Names}}'])
            if listing.returncode:raise RuntimeError('Cannot verify local CI container termination')
            for name in listing.stdout.splitlines():
                if CONTAINER.fullmatch(name):
                    killed=run(['docker','rm','-f',name],timeout=20)
                    if killed.returncode:raise RuntimeError('Could not terminate local CI container '+name)
            # Gunicorn's stopped user-service cgroup owns its command processes.
            for unit in active:
                if run(['systemctl','--user','is-active','--quiet',unit]).returncode==0:
                    raise RuntimeError('CI service still running; refusing destructive pruning')
            for row in rows:
                if row['scheduler_status'] in ('queued','leased','stranded'):
                    control=cancel(b,row['job_id'],'owner-hourly-reset')
                    if row['scheduler_status'] in ('leased','stranded'):
                        with b._db() as db:
                            db.execute('BEGIN IMMEDIATE')
                            db.execute('UPDATE jobs SET status="cancelled",lease_hash=NULL,lease_until=NULL,updated=? WHERE id=?',(time.time(),row['job_id']))
                            db.execute('UPDATE ci_bridge_events SET status="cancelled",detail="Owner reset; worker stopped and CI containers removed",updated=? WHERE job_id=?',(time.time(),row['job_id']))
                            db.commit()
                    out['cancelled_hub'].append(row['job_id'])
            candidates=[]
            legacy_logs=LEGACY/'logs'
            if legacy_logs.is_symlink():raise PermissionError('Unsafe legacy log root')
            if legacy_logs.is_dir():
                for p in legacy_logs.iterdir():
                    if p.is_file() and not p.is_symlink() and p.suffix=='.log':
                        candidates.append((p.stat().st_mtime,'legacy',p.name,p))
            for row in rows:
                p=ci_bridge.STATE/'logs'/(row['job_id']+'.log')
                candidates.append((row['created'],'hub',row['job_id'],p))
            retained=sorted(candidates,key=lambda item:(item[0],item[1],item[2]),reverse=True)[:keep]
            keys={(kind,key) for _,kind,key,_ in retained}
            out['retained']=[{'kind':kind,'run':key} for _,kind,key,_ in retained]
            for _,kind,key,path in candidates:
                if (kind,key) in keys:continue
                if path.is_symlink():raise PermissionError('Unsafe log file')
                if path.is_file():path.unlink();out['removed_logs']+=1
                if kind=='hub':
                    remove_private_tree(ci_bridge.STATE/'artifacts'/key)
                    remove_private_tree(ci_bridge.STATE/'work'/key)
                    with b._db() as db:
                        db.execute('BEGIN IMMEDIATE')
                        db.execute('DELETE FROM ci_job_controls WHERE job_id=?',(key,))
                        db.execute('DELETE FROM ci_bridge_events WHERE job_id=?',(key,))
                        db.execute('DELETE FROM jobs WHERE id=?',(key,))
                        db.commit()
                    out['removed_hub_records']+=1
            # Legacy workspaces are keyed by repo/short SHA, matching legacy log names.
            workroot=LEGACY/'workspace'
            if workroot.is_symlink():raise PermissionError('Unsafe legacy work root')
            retained_legacy={key[:-4] for kind,key in keys if kind=='legacy'}
            if workroot.is_dir():
                for repo_dir in workroot.iterdir():
                    if not repo_dir.is_dir() or repo_dir.is_symlink():continue
                    for work in repo_dir.iterdir():
                        if work.is_dir() and not work.is_symlink() and re.fullmatch(r'[a-f0-9]{7,40}',work.name):
                            if repo_dir.name+'-'+work.name not in retained_legacy:
                                remove_private_tree(work);out['removed_workspaces']+=1
            out['success']=True
        except Exception as exc:
            out['success']=False;out['error']=str(exc)[:300]
        finally:
            restored=[]
            for unit in active:
                restored.append(run(['systemctl','--user','start',unit],timeout=30).returncode==0)
            out['services_restored']=all(restored)
            out['finished']=time.time()
            secure_write(folder/'last-reset.json',json.dumps(out,indent=2,sort_keys=True)+'\n')
        return out

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--confirm',action='store_true');p.add_argument('--keep',type=int,default=2)
    a=p.parse_args()
    if not a.confirm:p.error('Owner-authorised cancellation and pruning require --confirm')
    result=reset(a.keep);print(json.dumps(result,indent=2))
    raise SystemExit(0 if result['success'] and result['services_restored'] else 1)
