#!/usr/bin/env python3
"""Reproducible, Git-tracked source tarball; sign only on an offline trusted PC."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
HEX=re.compile(r'^[a-f0-9]{40}$')

def build(ref,output=None):
    commit=subprocess.run(['git','rev-parse','--verify',ref+'^{commit}'],
        cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,check=True).stdout.strip()
    if not HEX.fullmatch(commit):
        raise RuntimeError('Invalid verified git commit.')
    path=Path(output) if output else ROOT/'dist'/('open-ci-hub-'+commit[:12]+'.tar.gz')
    path=path.resolve()
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() or path.is_symlink():
        raise RuntimeError('Release output already exists.')
    tar=subprocess.run(['git','archive','--format=tar','--prefix=open-ci-hub/',commit],
        cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True).stdout
    if not tar or len(tar)>80*1024*1024:
        raise RuntimeError('Git archive is empty or too large.')
    tmp=Path(tempfile.mkstemp(dir=path.parent,prefix='.distribution-')[1])
    try:
        with tmp.open('wb') as file:
            with gzip.GzipFile(filename='',fileobj=file,mode='wb',compresslevel=9,mtime=0) as gz:
                gz.write(tar)
        sha=hashlib.sha256(tmp.read_bytes()).hexdigest()
        os.replace(tmp,path)
        (Path(str(path)+'.sha256')).write_text(sha+'  '+path.name+'\n',encoding='utf-8')
    finally:
        if tmp.exists():tmp.unlink()
    return {'archive':str(path),'sha256':sha,'source_commit':commit,
            'signed':False,'message':'Sign outside CI with an approved offline key.'}

def main():
    import json
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('ref');p.add_argument('--output')
    args=p.parse_args()
    try:
        print(json.dumps(build(args.ref,args.output),indent=2))
        return 0
    except (OSError,RuntimeError,subprocess.CalledProcessError) as exc:
        print('Distribution:',str(exc)[:200],file=sys.stderr);return 1
if __name__=='__main__':
    raise SystemExit(main())
