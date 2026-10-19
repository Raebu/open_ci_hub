#!/usr/bin/env python3
"""Bounded opt-in native runner registrations for trusted personal GitHub repos.

GitHub personal repositories cannot share a single organisation runner. Register
separate native runners in isolated Docker volumes; never delete host state or
replace a remote runner automatically. No Docker socket is exposed to jobs.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote

import fleet
from hub import secure_write
from operations import invoke_json, safe_repo
from providers.github import prepare_target

ROOT = Path(__file__).resolve().parent
POLICY = ROOT / '.local' / 'runner-pool-scopes.json'
STATE = ROOT / '.local' / 'runner-pool.json'
IMAGE = 'open-ci-hub-github'
MAX_ACTIVE = 3
MAX_NEW = 4
PREFIX = 'open-ci-hub-pool-'

def _read(path: Path, kind):
    if path.is_symlink() or path.parent.is_symlink():
        raise RuntimeError('Refusing symlinked runner pool state.')
    if not path.exists():
        return kind()
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise RuntimeError('Corrupt runner-pool state. Recover it before changing registrations.') from None
    if not isinstance(value, kind):
        raise RuntimeError('Unexpected runner-pool state type.')
    return value

def _policy():
    data = _read(POLICY, dict)
    scopes = data.get('scopes', [])
    if not isinstance(scopes, list) or any(not isinstance(x,str) or not fleet.OWNER.fullmatch(x) for x in scopes):
        raise RuntimeError('Invalid approved runner scopes.')
    return data

def approved(owner):
    owner = fleet.validate_owner(owner)
    return owner.casefold() in {s.casefold() for s in _policy().get('scopes', [])}

def authorize(owner, confirm=False):
    owner = fleet.validate_owner(owner)
    if confirm is not True:
        raise PermissionError('Explicit --confirm required before creating runner credentials.')
    data = _policy()
    scopes = data.setdefault('scopes', [])
    if not approved(owner):
        scopes.append(owner)
    secure_write(POLICY, json.dumps(data, indent=2) + '\n')
    return data

def worker_names(repo):
    repo = safe_repo(repo)
    if len(repo.split('/')) != 2:
        raise ValueError('GitHub runner must target exactly OWNER/REPO.')
    digest = hashlib.sha256(repo.casefold().encode()).hexdigest()[:16]
    return PREFIX + digest, PREFIX + digest + '-state'

def _docker(args, *, extra_env=None, check=True):
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    try:
        result = subprocess.run(['docker', *args], check=False, text=True,
                                capture_output=True, env=env, timeout=120)
    except (OSError,subprocess.TimeoutExpired):
        raise RuntimeError('Docker command failed/timed out. Inspect runner state before retrying.') from None
    if check and result.returncode:
        raise RuntimeError('Docker operation failed; inspect local state and Docker logs before retrying.')
    return result.stdout.strip(), result.returncode

def _state():
    rows = _read(STATE, dict)
    if any(not isinstance(k,str) or not isinstance(v,dict) or
           v.get('container') != worker_names(k)[0] or
           v.get('volume') != worker_names(k)[1] for k,v in rows.items()):
        raise RuntimeError('Runner metadata is invalid; refusing changes.')
    return rows

def _configured(volume):
    value, code = _docker(['run','--rm','--network','none','--cap-drop','ALL',
                          '--security-opt','no-new-privileges:true',
                          '-v',volume+':/home/runner',IMAGE,
                          'sh','-c','test -f /home/runner/.runner'],check=False)
    return code == 0

def _existing_container(name):
    result, code = _docker(['inspect','--format','{{.State.Status}}',name],check=False)
    return result if code == 0 else None

def _active():
    output, _ = _docker(['ps','--filter','name=^/'+PREFIX,
                         '--format','{{.Names}}'])
    return {x.strip() for x in output.splitlines() if x.strip().startswith(PREFIX)}

def _trusted_workflow(repo, branch):
    # Recheck all workflows before allowing persistent personal-PC execution.
    tree = fleet._json_object(['gh','api',f'repos/{repo}/git/trees/{quote(branch,safe="")}?recursive=1'])
    if tree.get('truncated') is not False or not isinstance(tree.get('tree'), list):
        return False
    paths = [x.get('path') for x in tree['tree'] if isinstance(x,dict) and
             isinstance(x.get('path'),str) and x['path'].startswith('.github/workflows/') and
             x['path'].endswith(('.yml','.yaml'))]
    if not paths or len(paths) > fleet.MAX_FILES:
        return False
    found = False
    for path in paths:
        try:
            contents, _ = fleet._file(repo,branch,path)
            if not fleet.event_is_trusted_main(contents,branch):
                return False
            if '[self-hosted, linux, local-ci]' in contents:
                found = True
            elif fleet.RUNS_ON.search(contents):
                return False
        except RuntimeError:
            return False
    return found

def register(repo,branch):
    repo = safe_repo(repo)
    owner = repo.split('/')[0]
    if not approved(owner):
        raise PermissionError('Runner registrations require a separately approved owner scope.')
    if not _trusted_workflow(repo,branch):
        raise RuntimeError('Only default-branch-only self-hosted workflows may use the personal runner pool.')
    rows = _state()
    name, volume = worker_names(repo)
    if repo in rows:
        if _configured(volume):
            return 'already-registered'
        raise RuntimeError('Local runner metadata exists but the Docker volume is not configured.')
    _docker(['image','inspect',IMAGE])
    _, exists = _docker(['volume','inspect',volume],check=False)
    if exists == 0:
        raise RuntimeError('A runner state volume already exists without metadata. Recover it; do not replace.')
    _docker(['volume','create',volume])
    registration = prepare_target('https://github.com/'+repo,name)
    # Docker argv includes only environment variable names. The short-lived
    # configuration process receives the temporary token in its environment.
    # Docker administrators can still inspect credentials during registration.
    command = ('exec ./config.sh --unattended --url "$GH_URL" --token "$GH_TOKEN" '
               '--name "$GH_NAME" --labels local-ci --work _work')
    _docker(['run','--rm','--cap-drop','ALL',
             '--security-opt','no-new-privileges:true','--pids-limit','256',
             '--memory','2g','-v',volume+':/home/runner',
             '-e','GH_URL','-e','GH_TOKEN','-e','GH_NAME',
             IMAGE,'sh','-c',command],
            extra_env={'GH_URL':registration.target,'GH_TOKEN':registration.token,'GH_NAME':name})
    if not _configured(volume):
        raise RuntimeError('Native registration completed but persisted runner configuration was not confirmed.')
    rows[repo]={'container':name,'volume':volume,'branch':branch}
    secure_write(STATE,json.dumps(rows,indent=2)+'\n')
    return 'registered'

def start(repo):
    rows=_state()
    if repo not in rows:
        raise RuntimeError('Repository runner has not been registered.')
    info=rows[repo]
    if _existing_container(info['container']) == 'running':
        return 'running'
    if not _configured(info['volume']):
        raise RuntimeError('Persistent runner configuration missing. Registration recovery required.')
    if len(_active()) >= MAX_ACTIVE:
        raise RuntimeError('Worker capacity is full. Queue jobs until a worker is available.')
    prior = _existing_container(info['container'])
    if prior is not None:
        _docker(['start',info['container']])
    else:
        _docker(['run','-d','--name',info['container'],
                 '--restart','unless-stopped','--cap-drop','ALL',
                 '--security-opt','no-new-privileges:true',
                 '--pids-limit','256','--memory','2g','--cpus','2',
                 '-v',info['volume']+':/home/runner',
                 IMAGE,'./run.sh'])
    return 'started'

def stop(repo):
    rows=_state()
    if repo not in rows:
        raise ValueError('Runner not managed by this pool.')
    name=rows[repo]['container']
    if _existing_container(name)=='running':
        _docker(['stop','--time','60',name])
    return 'stopped'  # Preserves credentials and named volume.

def queued(repo,status):
    payload=invoke_json(['gh','api',f'repos/{repo}/actions/runs?status={status}&per_page=20'])
    if not isinstance(payload,dict) or not isinstance(payload.get('workflow_runs'),list):
        raise RuntimeError('Cannot determine remote job activity safely.')
    return bool(payload['workflow_runs'])

def schedule():
    rows=_state()
    running=_active()
    pending=[]
    for repo,info in rows.items():
        try:
            if queued(repo,'queued'):
                pending.append(repo)
        except RuntimeError:
            continue
    # Prefer queued jobs. Never interrupt a repo that has in-progress runs or
    # whose activity cannot be verified safely.
    for repo in pending:
        if rows[repo]['container'] in running:
            continue
        if len(running) >= MAX_ACTIVE:
            for victim,info in rows.items():
                if info['container'] not in running or victim in pending:
                    continue
                try:
                    if not queued(victim,'in_progress') and not queued(victim,'queued'):
                        stop(victim)
                        running.remove(info['container'])
                        break
                except RuntimeError:
                    continue
        if len(running) < MAX_ACTIVE:
            try:
                start(repo)
                running.add(rows[repo]['container'])
            except RuntimeError:
                pass
    return {'registered':len(rows),'active':len(running),'queued_repositories':len(pending)}

def sync(owner,apply=False,limit=MAX_NEW):
    owner=fleet.validate_owner(owner)
    if apply and not approved(owner):
        raise PermissionError('Approve runner-pool scope separately before provisioning.')
    if not 1<=limit<=MAX_NEW:
        raise ValueError('At most four new runner registrations per pass.')
    output, count=[],0
    for repo,branch in fleet.discover(owner):
        try:
            if not _trusted_workflow(repo,branch):
                continue
            rows=_state()
            if repo in rows:
                status='registered'
            elif apply and count<limit:
                status=register(repo,branch)
                count+=1
            else:
                status='eligible-unregistered'
            output.append({'repo':repo,'status':status})
        except (RuntimeError,ValueError,PermissionError) as exc:
            output.append({'repo':repo,'status':'blocked','reason':str(exc)})
    if apply:
        output.append({'schedule':schedule()})
    return output

def main(argv=None):
    parser=argparse.ArgumentParser(description='Bounded native GitHub per-repo runner pool.')
    cmd=parser.add_subparsers(dest='action',required=True)
    for action in ('authorize','scan','sync','schedule','start','stop'):
        p=cmd.add_parser(action)
        if action not in ('schedule',):
            p.add_argument('target')
        if action=='authorize':
            p.add_argument('--confirm',action='store_true')
        if action=='sync':
            p.add_argument('--apply',action='store_true')
            p.add_argument('--limit',type=int,default=MAX_NEW)
    args=parser.parse_args(argv)
    try:
        if args.action=='authorize':
            result=authorize(args.target,args.confirm)
        elif args.action=='schedule':
            result=schedule()
        elif args.action=='start':
            result=start(safe_repo(args.target))
        elif args.action=='stop':
            result=stop(safe_repo(args.target))
        else:
            result=sync(args.target,apply=args.action=='sync' and args.apply,
                        limit=getattr(args,'limit',MAX_NEW))
        print(json.dumps(result,indent=2))
        return 0
    except (RuntimeError,ValueError,PermissionError,OSError) as exc:
        print('Runner pool:',exc,file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
