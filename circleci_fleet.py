#!/usr/bin/env python3
"""Review-only CircleCI machine runner migrations in authorised GitHub sources."""
from __future__ import annotations
import argparse
from dataclasses import dataclass
import base64
import json
from pathlib import Path
import re
import secrets
import sys
import time
from urllib.parse import quote
import fleet
from onboarding import RESOURCE_CLASS, _json_object
from readiness import check as check_readiness
from hub import secure_write
from operations import safe_repo, invoke_json

ROOT=Path(__file__).resolve().parent
POLICY=ROOT/'.local'/'circleci-fleet-scopes.json'
SHA=re.compile(r'^[a-f0-9]{40}$')
JOB=re.compile(r'(?m)^  ([a-zA-Z][a-zA-Z0-9_-]{0,80}):\s*$')
MAX_PER_PASS=5

@dataclass(frozen=True)
class Plan:
    repo: str
    branch: str
    state: str
    reason: str
    content: str=''
    sha: str=''
    jobs: int=0
    resource_class: str=''

    def summary(self):
        return {'repo':self.repo,'branch':self.branch,'status':self.state,
                'reason':self.reason,'jobs':self.jobs}

def _policy():
    if POLICY.is_symlink() or POLICY.parent.is_symlink():
        raise RuntimeError('Symlinked CircleCI policy is prohibited.')
    if not POLICY.exists():
        return {'owners':{}}
    try:
        data=json.loads(POLICY.read_text(encoding='utf-8'))
    except (OSError,ValueError):
        raise RuntimeError('Corrupt CircleCI scope policy.') from None
    if not isinstance(data,dict) or not isinstance(data.get('owners'),dict):
        raise RuntimeError('Invalid CircleCI scope policy.')
    for name,resource in data['owners'].items():
        if not isinstance(name,str) or not fleet.OWNER.fullmatch(name) or \
           not isinstance(resource,str) or not RESOURCE_CLASS.fullmatch(resource):
            raise RuntimeError('Invalid owner/resource class in CircleCI policy.')
    return data

def authorize(owner,resource_class,confirm=False):
    owner=fleet.validate_owner(owner)
    if not isinstance(resource_class,str) or not RESOURCE_CLASS.fullmatch(resource_class):
        raise ValueError('CircleCI needs an authorised namespace/resource-class.')
    if confirm is not True:
        raise PermissionError('Explicit --confirm required before creating migration PRs.')
    data=_policy()
    data['owners'][owner]=resource_class
    secure_write(POLICY,json.dumps(data,indent=2)+'\n')
    return data

def resource_for(owner):
    owner=fleet.validate_owner(owner)
    for o,resource in _policy()['owners'].items():
        if o.casefold()==owner.casefold():
            return resource
    return None

def _extract_sections(content):
    lines=content.splitlines(keepends=True)
    root={line.rstrip('\n\r'):i for i,line in enumerate(lines)
          if line and not line.startswith((' ','#'))}
    if 'jobs:' not in root or 'workflows:' not in root or root['jobs:']>=root['workflows:']:
        raise RuntimeError('Expected conventional jobs before workflows in CircleCI config.')
    if re.search(r'(?m)^(orbs|executors|setup|parameters|triggers):',content):
        raise RuntimeError('Reusable executors, setup or triggers require manual review.')
    return lines,root['jobs:'],root['workflows:']

def migrate(content,branch,resource_class):
    if not RESOURCE_CLASS.fullmatch(resource_class) or '..' in resource_class:
        raise ValueError('Invalid native CircleCI resource class.')
    lines,start,end=_extract_sections(content)
    jobs=''.join(lines[start+1:end])
    definitions=list(JOB.finditer(jobs))
    if not definitions or len(definitions)>25:
        raise RuntimeError('No simple CircleCI jobs found.')
    # Workflow filters must limit execution to one default branch. Never route
    # public PR jobs or arbitrary feature branches to a persistent PC.
    workflows=''.join(lines[end+1:])
    if re.search(r'(?m)^\s{4}triggers:',workflows):
        raise RuntimeError('Scheduled/triggered workflows require manual approval.')
    refs=re.findall(r'(?m)^      - ([A-Za-z][A-Za-z0-9_-]{0,80}):\s*$',workflows)
    if not refs or set(refs)!={m.group(1) for m in definitions} or len(refs)!=len(definitions):
        raise RuntimeError('Complex or unfiltered workflow job references require manual review.')
    filters=re.findall(r'(?m)^          filters:\s*\n            branches:\s*\n              only:\s*(.+?)\s*$',workflows)
    if len(filters)!=len(refs) or any(value.strip().strip("'\"")!=branch for value in filters):
        raise RuntimeError('Workflows must run only on the repository default branch.')
    patches=[]
    for i,m in enumerate(definitions):
        body=jobs[m.end():definitions[i+1].start() if i+1<len(definitions) else len(jobs)]
        if not re.search(r'(?m)^    machine:\s*true\s*$',body):
            raise RuntimeError(f'Job {m.group(1)} is not an eligible native machine job.')
        if re.search(r'(?m)^    (?:docker|executor|resource_class|parallelism):',body):
            raise RuntimeError(f'Job {m.group(1)} has a custom executor/resource class.')
        if not re.search(r'(?m)^    steps:',body):
            raise RuntimeError(f'Job {m.group(1)} has no conventional build steps.')
        # Insert immediately after the existing machine: true line.
        marker=re.search(r'(?m)^    machine:\s*true\s*$',body)
        before=content[:content.index(jobs)+m.end()+marker.end()]
        pos=len(before)
        patches.append(pos)
    output=content
    for pos in reversed(patches):
        output=output[:pos]+'\n    resource_class: '+resource_class+output[pos:]
    return output,len(patches)

def assess(repo,branch,resource_class):
    repo=safe_repo(repo)
    if len(repo.split('/'))!=2:
        raise ValueError('CircleCI GitHub source must be OWNER/REPO.')
    tree=_json_object(['gh','api',f'repos/{repo}/git/trees/{quote(branch,safe="")}?recursive=1'])
    if tree.get('truncated') is not False or not isinstance(tree.get('tree'),list):
        return Plan(repo,branch,'manual','Incomplete GitHub repository tree.')
    paths={x.get('path') for x in tree['tree'] if isinstance(x,dict)}
    if '.circleci/config.yml' not in paths:
        return Plan(repo,branch,'no-workflow','CircleCI config is absent; use native onboarding.',resource_class=resource_class)
    try:
        contents,sha=fleet._file(repo,branch,'.circleci/config.yml')
        modified,jobs=migrate(contents,branch,resource_class)
    except RuntimeError as exc:
        return Plan(repo,branch,'manual',str(exc),resource_class=resource_class)
    return Plan(repo,branch,'review-ready','Existing steps preserved; native machine resource class proposed.',
                modified,sha,jobs,resource_class)

def has_pending_pr(repo):
    rows=invoke_json(['gh','api',f'repos/{repo}/pulls?state=open&per_page=100'])
    if not isinstance(rows,list) or len(rows)>=100:
        raise RuntimeError('Cannot inspect complete CircleCI migration PR listing.')
    return any(isinstance(x,dict) and isinstance(x.get('head'),dict)
               and str(x['head'].get('ref','')).startswith('open-ci-hub/circleci-') for x in rows)

def create_pr(plan):
    if plan.state!='review-ready' or not plan.jobs:
        raise ValueError('No eligible CircleCI CI migration.')
    if resource_for(plan.repo.split('/')[0]) != plan.resource_class:
        raise PermissionError('CircleCI owner and resource class not approved.')
    if has_pending_pr(plan.repo):
        raise RuntimeError('Existing CircleCI fleet PR pending.')
    ready=check_readiness('circleci',plan.repo,plan.branch,plan.resource_class)
    if not isinstance(ready,dict) or ready.get('verified') is not True:
        raise RuntimeError('Matching CircleCI native machine runner is unverified.')
    if assess(plan.repo,plan.branch,plan.resource_class)!=plan:
        raise RuntimeError('CircleCI config changed since assessment.')
    ref=_json_object(['gh','api',f'repos/{plan.repo}/git/ref/heads/{quote(plan.branch,safe="")}'])
    sha=(ref.get('object') or {}).get('sha')
    if not isinstance(sha,str) or not SHA.fullmatch(sha):
        raise RuntimeError('Cannot verify GitHub source head SHA.')
    name='open-ci-hub/circleci-'+secrets.token_hex(6)
    _json_object(['gh','api','-X','POST',f'repos/{plan.repo}/git/refs',
                  '-f',f'ref=refs/heads/{name}','-f',f'sha={sha}'])
    encoded=base64.b64encode(plan.content.encode('utf-8')).decode()
    _json_object(['gh','api','-X','PUT',f'repos/{plan.repo}/contents/.circleci/config.yml',
                  '-f','message=Route trusted CircleCI jobs to native machine runners',
                  '-f',f'content={encoded}','-f',f'sha={plan.sha}','-f',f'branch={name}'])
    pr=_json_object(['gh','api','-X','POST',f'repos/{plan.repo}/pulls',
                     '-f','title=Use native Open CI Hub machine resource class',
                     '-f',f'head={name}','-f',f'base={plan.branch}',
                     '-f','body=Review CircleCI resource class eligibility, plan/network quotas, toolchains and trusted main-only triggers. Original job steps preserved. This PR is never auto-merged.'])
    url=pr.get('html_url')
    if not isinstance(url,str) or not url.startswith(f'https://github.com/{plan.repo}/pull/'):
        raise RuntimeError('Review branch exists but CircleCI migration PR was not confirmed.')
    return url

def sync(owner,apply=False,limit=MAX_PER_PASS):
    owner=fleet.validate_owner(owner)
    resource_class=resource_for(owner)
    if apply and not resource_class:
        raise PermissionError('CircleCI resource-class scope must be explicitly approved.')
    if not 1<=limit<=MAX_PER_PASS:
        raise ValueError('Maximum five review PRs per pass.')
    results,created=[],0
    for repo,branch in fleet.discover(owner):
        try:
            if not resource_class:
                result={'repo':repo,'status':'not-authorized'}
            else:
                plan=assess(repo,branch,resource_class)
                result=plan.summary()
                if apply and plan.state=='review-ready' and created<limit:
                    if has_pending_pr(repo):
                        result['status']='pending-review'
                    else:
                        result.update(status='review-created',url=create_pr(plan))
                        created+=1
        except (RuntimeError,ValueError,PermissionError) as exc:
            result={'repo':repo,'status':'blocked','reason':str(exc)}
        results.append(result)
    return results

def main(argv=None):
    parser=argparse.ArgumentParser(description='Opt-in native CircleCI machine runner migration.')
    sub=parser.add_subparsers(dest='action',required=True)
    for name in ('authorize','scan','sync','watch'):
        p=sub.add_parser(name)
        p.add_argument('owner')
        if name=='authorize':
            p.add_argument('--resource-class',required=True)
            p.add_argument('--confirm',action='store_true')
        if name in ('sync','watch'):
            p.add_argument('--apply',action='store_true')
            p.add_argument('--limit',type=int,default=5)
        if name=='watch':
            p.add_argument('--interval',type=int,default=3600)
    args=parser.parse_args(argv)
    try:
        if args.action=='authorize':
            result=authorize(args.owner,args.resource_class,args.confirm)
        elif args.action=='watch':
            if not args.apply or not resource_for(args.owner) or not 600<=args.interval<=86400:
                raise ValueError('Watch requires approved resource class, --apply and 600-86400 seconds.')
            while True:
                print(json.dumps(sync(args.owner,apply=True,limit=args.limit)),flush=True)
                time.sleep(args.interval)
        else:
            result=sync(args.owner,apply=args.action=='sync' and args.apply,
                        limit=getattr(args,'limit',5))
        print(json.dumps(result,indent=2))
        return 0
    except KeyboardInterrupt:
        return 130
    except (RuntimeError,ValueError,PermissionError,OSError) as exc:
        print('CircleCI fleet:',exc,file=sys.stderr)
        return 1

if __name__=='__main__':
    raise SystemExit(main())
