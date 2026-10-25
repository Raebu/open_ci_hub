#!/usr/bin/env python3
"""Owner-authorised reversible Git access drill; never changes production refs.

GitLab pushes explicitly skip provider CI. GitHub requires a reviewed repository
with local-only workflow rules. Evidence contains IDs/hashes, never credentials.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ci_bridge import repository_clone_argv, REPO
from ci_execution import redact
from hub import secure_write


def verify(provider, repo, evidence):
    if provider not in ('github','gitlab') or not REPO.fullmatch(repo):
        raise ValueError('Exact approved provider and owner/repository required')
    branch='open-ci-hub-access-test-'+secrets.token_hex(8)
    result={'provider':provider,'repository':repo,'branch':branch,'started':time.time(),
            'steps':[],'cleanup_confirmed':False,'success':False}
    env={**os.environ,'GIT_TERMINAL_PROMPT':'0','GCM_INTERACTIVE':'never'}
    helper='!'+('gh' if provider=='github' else 'glab')+' auth git-credential'
    base=["git","-c","credential.helper=","-c","credential.helper="+helper]
    pushed=False
    def run(argv,cwd=None):
        r=subprocess.run(argv,cwd=cwd,env=env,stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=90)
        if r.returncode:
            raise RuntimeError('Git access drill failed: '+redact(r.stdout)[-800:])
        return r.stdout.strip()
    try:
        with tempfile.TemporaryDirectory(prefix='git-access-',dir=Path(evidence).parent) as folder:
            work=Path(folder)/'repo'
            run(repository_clone_argv(provider,repo,work))
            run(base+['checkout','--detach','origin/HEAD'],work)
            result['baseline']=run(['git','rev-parse','HEAD'],work)
            result['steps'].append('private-checkout')
            run(['git','switch','-c',branch],work)
            (work/'OPEN_CI_HUB_ACCESS_TEST.txt').write_text('Disposable authorised Open CI Hub access test.\n')
            run(['git','add','--','OPEN_CI_HUB_ACCESS_TEST.txt'],work)
            run(['git','-c','user.name=Open CI Hub access verification',
                 '-c','user.email=open-ci-hub@localhost','-c','commit.gpgsign=false',
                 'commit','-m','Verify local Hub repository access [skip ci]'],work)
            commit=run(['git','rev-parse','HEAD'],work);result['commit']=commit
            base=['git','-c','credential.helper=','-c','credential.helper='+helper]
            push=base+['push']+(['-o','ci.skip'] if provider=='gitlab' else [])
            try:
                # A timeout may occur after the server accepts a push. Always clean the unique ref.
                pushed=True
                run(push+['origin','HEAD:refs/heads/'+branch],work)
                remote=run(base+['ls-remote','origin','refs/heads/'+branch],work)
                if not remote.startswith(commit+'\t'):
                    raise RuntimeError('Remote test branch did not match committed source')
                result['steps']+=['branch-created','committed','pushed','remote-sha-verified']
                result['success']=True
            finally:
                if pushed:
                    run(push+['origin',':refs/heads/'+branch],work)
                    if run(base+['ls-remote','origin','refs/heads/'+branch],work):
                        raise RuntimeError('Temporary remote branch remains')
                    result['cleanup_confirmed']=True
    except (OSError,RuntimeError,subprocess.TimeoutExpired) as exc:
        result['success']=False;result['error']=redact(str(exc))[:1000]
    finally:
        result['finished']=time.time()
        secure_write(Path(evidence),json.dumps(result,sort_keys=True,indent=2)+'\n')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('provider',choices=['github','gitlab']);p.add_argument('repository')
    p.add_argument('--evidence',required=True,type=Path)
    p.add_argument('--confirm',action='store_true')
    a=p.parse_args()
    if not a.confirm:p.error('Owner authorisation required: --confirm')
    a.evidence.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    r=verify(a.provider,a.repository,a.evidence)
    print(json.dumps(r,indent=2));sys.exit(0 if r['success'] and r['cleanup_confirmed'] else 1)
