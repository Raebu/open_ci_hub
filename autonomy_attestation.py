#!/usr/bin/env python3
"""Ed25519-signed independent-verifier-ready mission evidence manifest.

This is an owner's cryptographic receipt, NOT a third-party attestation.
Private keys never leave the owner machine or enter a job/environment.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from datetime import datetime, timezone
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey,Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,PublicFormat,NoEncryption

from autonomy_core import Journal,canonical,ID

def _private(path):
    path=Path(path).absolute()
    if path.is_symlink():
        raise ValueError("Symlinked evidence/key file refused.")
    if path.is_file() and (path.stat().st_mode & 0o077):
        raise PermissionError("Private evidence/key file has unsafe permissions.")
    return path

def _write_atomic(path,blob):
    path=_private(path)
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.exists():
        raise FileExistsError("Evidence already exists; never silently overwrite.")
    fd,tmp=tempfile.mkstemp(prefix=".evidence-",dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"wb") as out:
            out.write(blob);out.flush();os.fsync(out.fileno())
        if path.exists():
            raise FileExistsError("Concurrent evidence already exists.")
        os.link(tmp,path) # atomic no-clobber hardlink creation
    finally:
        Path(tmp).unlink(missing_ok=True)

def make_key(private_path,*,confirmed=False):
    if confirmed is not True:
        raise PermissionError("Creating an attestation identity requires owner confirmation.")
    path=_private(private_path)
    if path.exists():
        raise FileExistsError("Existing signing identity cannot be replaced.")
    key=Ed25519PrivateKey.generate()
    raw=key.private_bytes(Encoding.Raw,PrivateFormat.Raw,NoEncryption())
    _write_atomic(path,raw)
    pub=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    return pub.hex()

def manifest_from_journal(journal,mission):
    if not ID.fullmatch(mission):
        raise ValueError("Invalid mission ID.")
    status=journal.snapshot(mission)
    if status["status"] not in ("completed","failed"):
        raise RuntimeError("Only terminal mission outcomes may be attested.")
    evidence=[]
    for step in status["steps"]:
        if step["status"] not in ("completed","failed"):
            continue
        result=json.loads(step["result"])
        digest=hashlib.sha256(canonical(result).encode()).hexdigest()
        if digest!=step["evidence_sha256"]:
            raise ValueError("Mission evidence digest mismatch; do not sign.")
        evidence.append({"step":step["id"],"status":step["status"],"sha256":digest})
    return {"schema":"open-ci-hub.attestation.v1","mission":mission,
            "status":status["status"],"plan_sha256":hashlib.sha256(
                canonical(status["plan"]).encode()).hexdigest(),"steps":evidence}

def sign_manifest(manifest,private_path,output_path,*,confirmed=False):
    if confirmed is not True:
        raise PermissionError("Owner must explicitly confirm an evidence signature.")
    key_data=_private(private_path).read_bytes()
    if len(key_data)!=32:
        raise ValueError("Invalid private signing key.")
    key=Ed25519PrivateKey.from_private_bytes(key_data)
    wire=canonical(manifest).encode()
    signature=key.sign(wire)
    public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    receipt={"manifest":manifest,"signature":signature.hex(),
             "public_key":public.hex(),"algorithm":"Ed25519"}
    _write_atomic(output_path,canonical(receipt).encode())
    return {"file":str(output_path),"manifest_sha256":hashlib.sha256(wire).hexdigest(),
            "verifier_public_key":public.hex()}

def verify_receipt(receipt_path,*,expected_public_key):
    if not isinstance(expected_public_key,str) or len(expected_public_key)!=64:
        return False
    path=Path(receipt_path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>20_000:
        return False
    try:
        record=json.loads(path.read_bytes())
        if set(record)!={"manifest","signature","public_key","algorithm"}:
            return False
        if record["algorithm"]!="Ed25519" or record["public_key"]!=expected_public_key:
            return False
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(expected_public_key)).verify(
            bytes.fromhex(record["signature"]),canonical(record["manifest"]).encode())
        return True
    except (ValueError,TypeError,KeyError,UnicodeError):
        return False
    except Exception:
        return False

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state",type=Path,default=Path(__file__).resolve().parent/".local"/"autonomy")
    sub=parser.add_subparsers(dest="cmd",required=True)
    p=sub.add_parser("init-key");p.add_argument("--confirm",action="store_true")
    s=sub.add_parser("sign");s.add_argument("mission");s.add_argument("--confirm",action="store_true")
    v=sub.add_parser("verify");v.add_argument("receipt",type=Path)
    v.add_argument("--public-key",required=True)
    args=parser.parse_args(argv)
    folder=args.state.absolute()
    key=folder/"evidence-key.bin"
    try:
        if args.cmd=="init-key":
            result={"public_key":make_key(key,confirmed=args.confirm)}
        elif args.cmd=="sign":
            journal=Journal(folder)
            manifest=manifest_from_journal(journal,args.mission)
            result=sign_manifest(manifest,key,folder/"attestations"/(args.mission+".json"),
                                  confirmed=args.confirm)
        else:
            result={"valid":verify_receipt(args.receipt,expected_public_key=args.public_key)}
        print(json.dumps(result,indent=2))
        return 0 if args.cmd!="verify" or result["valid"] else 2
    except (ValueError,PermissionError,OSError,RuntimeError,FileExistsError) as exc:
        print("Attestation refused: "+str(exc)[:100])
        return 2

if __name__=="__main__":
    raise SystemExit(main())
