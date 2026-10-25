#!/usr/bin/env python3
"""Run a pre-approved app handler inside a systemd RAM-limited transient user service.

Host owner installs handlers. API payload is JSON passed to stdin; no shell.
Linux cgroups v2 enforce MemoryMax/MemorySwapMax when systemd permits scope
creation. GPU VRAM is NOT hard-capped; GPU jobs use the applicable vendor probe
and scheduler reservations. Child stdout is bounded without restricting app files.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from application_client import ApplicationClient
from bounded_output import exchange
from resource_guard import gpu_memory_inventory, memory_available_mb

MAX_HANDLER_BYTES=16384
MAX_HANDLER_SECONDS=1800
NAME=re.compile(r'^[a-z][a-z0-9_.-]{1,64}$')
EXE=re.compile(r'^/[A-Za-z0-9_./+-]+$')

def load_handlers(path:Path):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_HANDLER_BYTES:
        raise RuntimeError('Missing or unsafe local handler manifest.')
    if os.name!='posix' or os.geteuid()==0 or path.stat().st_uid!=os.geteuid() or path.stat().st_mode & 0o077:
        raise PermissionError('Handlers must be private, host-user-owned and never run as root.')
    data=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data,dict) or not 1<=len(data)<=30:
        raise ValueError('Handler manifest must contain 1–30 tasks.')
    handlers={}
    for task,item in data.items():
        if not isinstance(task,str) or not NAME.fullmatch(task) or not isinstance(item,dict):
            raise ValueError('Invalid handler identifier.')
        argv=item.get('argv');timeout=item.get('timeout',240)
        if (not isinstance(argv,list) or not 1<=len(argv)<=12 or
            any(not isinstance(x,str) or not x or len(x)>300
                or any(ord(ch)<32 for ch in x) for x in argv) or
            not EXE.fullmatch(argv[0]) or '..' in Path(argv[0]).parts or
            type(timeout)!=int or not 10<=timeout<=MAX_HANDLER_SECONDS):
            raise ValueError('Handler argv or execution timeout invalid.')
        if not Path(argv[0]).is_file() or not os.access(argv[0],os.X_OK):
            raise RuntimeError('Allowlisted handler executable not installed.')
        handlers[task]={'argv':argv,'timeout':timeout}
    return handlers

def build_command(job,handler):
    """Only static host-approved argv; never interpolate untrusted payload."""
    budget=job['resources']
    ram=budget['ram_mb']
    if type(ram)!=int or not 64<=ram<=262144:
        raise RuntimeError('Claim lacks valid RAM reservation.')
    if budget.get('gpu_memory_mode')=='shared':
        shared=budget.get('vram_mb')
        if type(shared)!=int or not 1<=shared<=262144 or ram+shared>262144:
            raise RuntimeError('Invalid Intel integrated GPU shared-RAM allocation.')
        ram+=shared
    # The same MemoryMax applies to CPU and Intel GPU shared-RAM work.
    unit='open-ci-app-'+job['job_id']
    if not re.fullmatch(r'open-ci-app-[a-f0-9]{32}',unit):
        raise RuntimeError('Invalid unique worker unit.')
    # On systemd 257+, --scope and --pipe are mutually incompatible.
    # A --wait transient user service preserves stdin/stdout piping,
    # propagates the exact handler exit status and retains cgroup limits.
    args=['/usr/bin/systemd-run','--user','--wait','--quiet',
          '--collect','--pipe','--unit='+unit,
          '-p','MemoryMax='+str(ram)+'M',
          '-p','MemorySwapMax=0',
          '-p','MemoryHigh='+str(max(64,ram*85//100))+'M',
          '-p','CPUQuota=150%',
          '-p','CPUWeight=50',
          '-p','TasksMax=128',
          '--',*handler['argv']]
    return unit+'.service',args

def stop_scope(unit,process):
    """Terminate entire transient service, not only the systemd-run wrapper."""
    try:
        subprocess.run(['/usr/bin/systemctl','--user','--no-ask-password',
                        'stop',unit],stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=12,
                       check=False)
    except (OSError,subprocess.TimeoutExpired):
        pass
    if process.poll() is None:
        process.terminate()
        try:process.wait(timeout=3)
        except subprocess.TimeoutExpired:process.kill()

def scope_confirmed_inactive(unit):
    """A wrapper exiting is not evidence every systemd scope child has exited."""
    if not re.fullmatch(r'open-ci-app-[a-f0-9]{32}\.service',unit):
        return False
    try:
        r=subprocess.run(['/usr/bin/systemctl','--user','--no-ask-password',
              'show',unit,'--property=ActiveState','--property=ControlGroup',
              '--property=LoadState','--no-pager'],capture_output=True,
              text=True,timeout=5,check=False)
        props=dict(line.split('=',1) for line in r.stdout.splitlines() if '=' in line)
        return (r.returncode==0 and props.get('ActiveState') in ('inactive','failed')
                and props.get('ControlGroup')==''
                and props.get('LoadState') in ('loaded','not-found'))
    except (OSError,subprocess.TimeoutExpired,ValueError):
        return False

def run_one(client,claim,handlers,*,telemetry=gpu_memory_inventory,
            ram_telemetry=memory_available_mb):
    job=claim.get('job')
    if not job:return {'status':'idle','reason':claim.get('reason')}
    task=job['task'];jid=job['job_id']
    token=job['lease_token']
    handler=handlers.get(task)
    if handler is None:
        # No execution. Explicitly fail instead of running arbitrary payload.
        return client.complete(jid,token,False,{'error':'Task handler not locally approved.'})
    budget=job['resources']
    unit,args=build_command(job,handler)
    gpu=budget['gpu_index']
    reserve=budget['vram_mb']
    gpu_mode=budget.get('gpu_memory_mode','dedicated') if reserve else None
    ram_floor=budget.get('min_free_ram_mb',2048)
    if type(ram_floor)!=int or ram_floor<256 or ram_floor>262144:
        return client.complete(jid,token,False,{'error':'Invalid machine RAM reserve.'})
    if reserve:
        live=telemetry().get(gpu)
        if not live or live.get('mode','dedicated')!=gpu_mode:
            return client.complete(jid,token,False,{'error':'GPU vendor/mode sensor unavailable or mismatched.'})
        if gpu_mode=='shared':
            # Never ask for NVIDIA VRAM on an Intel integrated GPU.
            # The reservation is backed entirely by physical system memory.
            available=ram_telemetry()
            if available is None or available < ram_floor+budget['ram_mb']+reserve:
                return client.complete(jid,token,False,{'error':'Intel shared GPU would breach system RAM headroom.'})
        elif gpu_mode=='dedicated':
            if live['free_mb'] < reserve+1024:
                return client.complete(jid,token,False,{'error':'Dedicated GPU memory or telemetry unavailable.'})
        else:
            return client.complete(jid,token,False,{'error':'Unsupported GPU memory type.'})
    payload=json.dumps({'job_id':jid,'task':task,'payload':job['payload'],
                        'attempt':job['attempt']},separators=(',',':'))
    env={k:v for k,v in os.environ.items() if k not in (
        'OPEN_CI_WORKER_TOKEN','OPEN_CI_APP_TOKEN','GITHUB_TOKEN','CI_JOB_TOKEN',
        'CUDA_VISIBLE_DEVICES','OPEN_CI_INTEL_SHARED_GPU')}
    if reserve and gpu_mode=='dedicated':
        env['CUDA_VISIBLE_DEVICES']=str(gpu)
    elif reserve and gpu_mode=='shared':
        env['OPEN_CI_INTEL_SHARED_GPU']='1'
    process=None;stop=threading.Event();failure=[]
    def watch():
        next_heartbeat=time.monotonic()+60
        while not stop.wait(1):
            if process.poll() is not None:return
            if reserve:
                reading=telemetry().get(gpu)
                if reading is None or reading.get('mode','dedicated')!=gpu_mode:
                    failure.append('GPU telemetry or vendor changed')
                    stop_scope(unit,process);return
                if gpu_mode=='shared':
                    available=ram_telemetry()
                    if available is None or available<ram_floor:
                        failure.append('Intel shared GPU exhausted safe system RAM headroom')
                        stop_scope(unit,process);return
                elif reading['free_mb']<1024:
                    failure.append('Dedicated GPU VRAM headroom breach')
                    stop_scope(unit,process);return
            if time.monotonic()>=next_heartbeat:
                try:client.heartbeat(jid,token)
                except (RuntimeError,OSError,ValueError):
                    failure.append('Worker lease heartbeat failed')
                    stop_scope(unit,process);return
                next_heartbeat=time.monotonic()+60

    try:
        process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT,env=env,cwd=str(Path.home()),
                                 start_new_session=True)
    except OSError:
        return client.complete(jid,token,False,{'error':'systemd user service unavailable.'})
    supervisor=threading.Thread(target=watch,daemon=True)
    supervisor.start()
    try:
        exchange_result=exchange(process,payload.encode(),handler['timeout'],
                                  terminate=lambda:stop_scope(unit,process))
    finally:
        stop.set()
        supervisor.join(timeout=5)
    output=exchange_result['output']
    reason=exchange_result['reason']
    if failure or reason:
        # Never acknowledge and release a lease after a kill unless the
        # systemd scope really ended. It remains reserved/stranded otherwise.
        if not scope_confirmed_inactive(unit):
            return {'status':'stranded','reason':'service_termination_unverified'}
        return client.complete(jid,token,False,{'error':(failure[0] if failure else reason)})
    if process.returncode:
        return client.complete(jid,token,False,{'error':'Approved application handler returned nonzero.'})
    if len(output)>4096:
        return client.complete(jid,token,False,{'error':'Worker response too large.'})
    try:
        result=json.loads(output)
        if not isinstance(result,(dict,list)):
            raise ValueError('JSON object/array result required.')
    except (ValueError,UnicodeDecodeError):
        return client.complete(jid,token,False,{'error':'Worker response was not valid JSON.'})
    return client.complete(jid,token,True,result)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--handlers',required=True,type=Path)
    p.add_argument('--url',default='http://127.0.0.1:8765')
    p.add_argument('--once',action='store_true')
    args=p.parse_args()
    if os.name!='posix' or os.geteuid()==0:
        print('Managed workers require non-root Linux and systemd user scope.',file=sys.stderr)
        return 2
    token=os.environ.get('OPEN_CI_WORKER_TOKEN','')
    try:
        handlers=load_handlers(args.handlers)
        client=ApplicationClient(args.url,token)
        while True:
            status=run_one(client,client.claim(),handlers)
            print('Worker state: '+str(status.get('status','unknown')),flush=True)
            if args.once:return 0 if status.get('status') in ('completed','idle') else 1
            if status.get('status')=='idle':time.sleep(5)
    except (RuntimeError,ValueError,PermissionError,OSError,KeyError) as exc:
        print('Managed worker:',str(exc)[:200],file=sys.stderr)
        return 1

if __name__=='__main__':
    raise SystemExit(main())
