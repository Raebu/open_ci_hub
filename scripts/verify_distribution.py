#!/usr/bin/env python3
"""Verify signed distribution bytes using an independently approved SSH public key."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

IDENTITY=re.compile(r'^[A-Za-z0-9._@+-]{3,150}$')

def verify(archive,signature,identity,allowed_signers):
    if not isinstance(identity,str) or not IDENTITY.fullmatch(identity):
        raise ValueError('Invalid signer identity.')
    files=[Path(p) for p in (archive,signature,allowed_signers)]
    if any(x.is_symlink() or not x.is_file() for x in files):
        raise RuntimeError('Invalid distribution, signature or approved public key file.')
    if files[0].stat().st_size>30*1024*1024 or files[1].stat().st_size>10000:
        raise RuntimeError('Distribution exceeds safe limits.')
    data=files[0].read_bytes()
    try:
        done=subprocess.run(['ssh-keygen','-Y','verify','-f',str(files[2]),'-I',identity,
            '-n','open-ci-hub-deploy','-s',str(files[1])],
            input=data,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            timeout=20,check=False)
    except (OSError,subprocess.TimeoutExpired):
        raise RuntimeError('Offline signature verification unavailable.') from None
    if done.returncode:
        raise RuntimeError('Distribution signature is invalid or signer is not approved.')
    return {'verified_signature':True,'signer':identity,
            'sha256':hashlib.sha256(data).hexdigest(),
            'message':'These bytes match an approved offline signer. Execution proof is separate.'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('archive','signature','signer','approved_signers'):
        p.add_argument(name)
    args=p.parse_args()
    try:
        print(json.dumps(verify(args.archive,args.signature,args.signer,args.approved_signers),indent=2))
        return 0
    except (ValueError,RuntimeError,OSError) as exc:
        print('Distribution:',str(exc),file=sys.stderr);return 1
if __name__=='__main__':
    raise SystemExit(main())
