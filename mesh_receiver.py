#!/usr/bin/env python3
"""At-most-once signed tiny-task receipt over an owner-controlled offline transport.

Transport is external (e.g. trusted LAN SSH/USB); cloud/API, arbitrary commands,
passwords and mutable tasks are not accepted. The SQLite ledger survives reboot.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import hashlib

from cryptography.exceptions import InvalidSignature
from offline_mesh_envelope import verify,ALLOWED
from autonomy_core import canonical

def receive(record,*,public_key:bytes,receiver:str,folder:Path,execute=False,
            runner=None,now=None):
    folder=Path(folder)
    if folder.is_symlink():
        raise PermissionError('Symlinked device state is prohibited.')
    folder.mkdir(mode=0o700,parents=True,exist_ok=True)
    if folder.stat().st_mode & 0o077:
        raise PermissionError('Device state directory must be private.')
    if type(execute) is not bool:
        raise ValueError('Execution permission must be explicit.')
    if not isinstance(record,dict) or len(canonical(record))>8192:
        raise ValueError('Oversized or invalid signed envelope.')
    now=int(time.time()) if now is None else int(now)
    approved=verify(record,pubkey=public_key,receiver=receiver,seen=set(),now=now)
    payload=record['payload']
    if payload['task'] not in ALLOWED or not isinstance(payload['body'],dict):
        raise ValueError('Only fixed, bounded, typed pure tasks may be transferred.')
    dbpath=folder/'mesh-receipts.sqlite'
    if dbpath.is_symlink():
        raise PermissionError('Symlinked receipt database refused.')
    db=sqlite3.connect(str(dbpath),isolation_level=None,timeout=5)
    try:
        if os.name!='nt':
            os.chmod(dbpath,0o600)
        db.execute('''CREATE TABLE IF NOT EXISTS receipts (
             sender TEXT NOT NULL, nonce TEXT NOT NULL, digest TEXT NOT NULL,
             state TEXT NOT NULL, result_sha256 TEXT, PRIMARY KEY(sender,nonce))''')
        db.execute('BEGIN IMMEDIATE')
        existing=db.execute('SELECT digest,state,result_sha256 FROM receipts '
                            'WHERE sender=? AND nonce=?',
                            (payload['sender'],payload['nonce'])).fetchone()
        if existing:
            db.rollback()
            if existing[0]!=record['digest']:
                raise ValueError('Conflicting replay detected.')
            return {'accepted':True,'duplicate':True,'state':existing[1],
                    'result_sha256':existing[2]}
        state='reserved' if execute else 'verified_not_executed'
        db.execute('INSERT INTO receipts(sender,nonce,digest,state) VALUES(?,?,?,?)',
                   (payload['sender'],payload['nonce'],record['digest'],state))
        db.commit()
        if not execute:
            return {'accepted':True,'duplicate':False,'state':state,
                    'digest':record['digest']}
        if runner is None:
            from edge_worker import execute as runner
        success,result=runner(payload['task'],payload['body'],128)
        result_wire=canonical({'success':success,'result':result}).encode()
        if len(result_wire)>8192:
            raise ValueError('Untrusted oversized worker result.')
        result_hash=hashlib.sha256(result_wire).hexdigest()
        # Reserved receipt remains if the device is interrupted. No replay.
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE receipts SET state=?,result_sha256=? '
                   'WHERE sender=? AND nonce=? AND state=?',
                   ('completed' if success else 'failed',result_hash,
                    payload['sender'],payload['nonce'],'reserved'))
        db.commit()
        return {'accepted':True,'duplicate':False,
                'state':'completed' if success else 'failed',
                'result_sha256':result_hash,'success':bool(success)}
    finally:
        db.close()

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receiver',required=True)
    parser.add_argument('--public-key',type=Path,required=True)
    parser.add_argument('--state',type=Path,default=Path.home()/'.config/open-ci-hub')
    parser.add_argument('--execute-pure',action='store_true')
    args=parser.parse_args(argv)
    try:
        pub=args.public_key
        if pub.is_symlink() or not pub.is_file() or pub.stat().st_size!=32 or pub.stat().st_mode & 0o077:
            raise PermissionError('Trusted sender key must be an owner-private 32-byte file.')
        wire=sys.stdin.buffer.read(8193)
        if len(wire)>8192:
            raise ValueError('Envelope exceeds transport limit.')
        record=json.loads(wire)
        result=receive(record,public_key=pub.read_bytes(),receiver=args.receiver,
                       folder=args.state,execute=args.execute_pure)
        print(json.dumps(result,sort_keys=True,separators=(',',':')))
        return 0 if result.get('accepted') and result.get('state') in ('completed','verified_not_executed') else 2
    except (OSError,ValueError,TypeError,PermissionError,sqlite3.Error,InvalidSignature,KeyError) as exc:
        # No credential or body details in errors.
        print(json.dumps({'accepted':False,'reason':type(exc).__name__}))
        return 2

if __name__=='__main__':
    raise SystemExit(main())
