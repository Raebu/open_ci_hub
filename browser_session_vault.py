#!/usr/bin/env python3
"""Per-host encrypted browser session cache (Playwright storageState, never passwords).

Secrets use the desktop Secret Service via libsecret. The 256-bit encryption
key is NOT stored in Git or any local plaintext key file. Fail closed if the
keyring is locked. Cookies are still bearer credentials: use owner approval.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from autonomy_core import valid_host

HOME_DIR=Path.home()/".local"/"share"/"open-ci-hub"/"sessions"
KEY_LABEL="Open CI Hub owner-encrypted browser sessions"
ATTR=["application","open-ci-hub","purpose","browser-sessions-v1"]
MAX_BYTES=262144
MAX_AGE=7*86400

def secret_key(*,init=False,confirm=False,run=subprocess.run):
    def call(args,**kwargs):
        try:
            return run(args,capture_output=True,timeout=12,check=False,**kwargs)
        except (OSError,subprocess.TimeoutExpired):
            raise RuntimeError("Desktop Secret Service unavailable or locked.") from None
    result=call(["/usr/bin/secret-tool","lookup",*ATTR])
    if result.returncode==0 and result.stdout.strip():
        try:
            key=bytes.fromhex(result.stdout.strip().decode("ascii"))
        except (UnicodeError,ValueError):
            raise RuntimeError("Invalid desktop keyring entry.") from None
        if len(key)!=32:
            raise RuntimeError("Invalid desktop keyring length.")
        if init:
            raise FileExistsError("Existing key will not be overwritten.")
        return key
    if not init or confirm is not True:
        raise PermissionError("Unlock desktop key store; initialise it with init --confirm.")
    key=secrets.token_bytes(32)
    created=call(["/usr/bin/secret-tool","store","--label="+KEY_LABEL,*ATTR],
                 input=key.hex().encode("ascii"))
    if created.returncode:
        raise RuntimeError("Desktop key store refused session key.")
    # Read-back confirms a real protected keyring, not a silent write failure.
    verified=call(["/usr/bin/secret-tool","lookup",*ATTR])
    if verified.returncode or verified.stdout.strip()!=key.hex().encode():
        raise RuntimeError("Desktop session key was not persisted.")
    return key

def _folder(folder=HOME_DIR):
    folder=Path(folder)
    if folder.is_symlink():
        raise PermissionError("Symlinked session directory rejected.")
    folder.mkdir(parents=True,mode=0o700,exist_ok=True)
    meta=folder.stat()
    if not folder.is_dir() or meta.st_uid!=os.getuid() or meta.st_mode&0o077:
        raise PermissionError("Session vault directory must be owner-only.")
    return folder

def _path(host,folder):
    valid_host(host)
    return _folder(folder)/(host+".session.enc")

def _state(host,data):
    valid_host(host)
    if not isinstance(data,dict) or set(data)!={"cookies","origins"}:
        raise ValueError("Only Playwright cookies/origins storage state is supported.")
    cookies,origins=data["cookies"],data["origins"]
    if not isinstance(cookies,list) or len(cookies)>128 or not isinstance(origins,list) or len(origins)>16:
        raise ValueError("Browser session exceeded scope.")
    for cookie in cookies:
        if (not isinstance(cookie,dict) or cookie.get("domain")!=host or
            cookie.get("secure") is not True or
            not isinstance(cookie.get("name"),str) or
            not isinstance(cookie.get("value"),str)):
            raise ValueError("Session cookie is outside exact HTTPS host.")
    for origin in origins:
        if (not isinstance(origin,dict) or origin.get("origin")!="https://"+host
            or not isinstance(origin.get("localStorage"),list)):
            raise ValueError("Local storage is outside exact HTTPS host.")
    wire=json.dumps(data,separators=(",",":"),allow_nan=False).encode()
    if len(wire)>MAX_BYTES:
        raise ValueError("Session exceeds bounded size.")
    return wire

def _owned_file(path):
    if (path.is_symlink() or not path.is_file() or
        path.stat().st_uid!=os.getuid() or path.stat().st_mode&0o077):
        raise PermissionError("Only owner-private regular session files allowed.")
    if path.stat().st_size>2*MAX_BYTES+4096:
        raise ValueError("Session file too large.")
    return path

def save(host,data,*,key=None,folder=HOME_DIR,now=None,ttl=86400,confirm=False):
    if confirm is not True:
        raise PermissionError("Owner must approve saving this authenticated session.")
    if type(ttl) is not int or not 60<=ttl<=MAX_AGE:
        raise ValueError("Unapproved session lifetime.")
    _state(host,data)
    key=secret_key() if key is None else key
    if not isinstance(key,bytes) or len(key)!=32:
        raise ValueError("Invalid encryption identity.")
    now=int(time.time()) if now is None else int(now)
    plaintext=json.dumps({"host":host,"issued":now,"expires":now+ttl,"state":data},
                         separators=(",",":")).encode()
    nonce=secrets.token_bytes(12)
    ciphertext=AESGCM(key).encrypt(nonce,plaintext,host.encode())
    record=json.dumps({"v":1,"expires":now+ttl,"n":nonce.hex(),"c":ciphertext.hex()},
                      separators=(",",":")).encode()
    path=_path(host,folder)
    if path.exists() or path.is_symlink():
        raise FileExistsError("Session exists; revoke before replacement.")
    fd,tmp=tempfile.mkstemp(prefix=".sealed-",dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"wb") as f:
            f.write(record);f.flush();os.fsync(f.fileno())
        os.link(tmp,path)  # refuses existing file, including concurrent writer
    finally:
        Path(tmp).unlink(missing_ok=True)
    return {"site":host,"expires":now+ttl,"encrypted":True}

def load(host,*,key=None,folder=HOME_DIR,now=None):
    path=_owned_file(_path(host,folder))
    record=json.loads(path.read_bytes())
    if not isinstance(record,dict) or set(record)!={"v","expires","n","c"} or record["v"]!=1:
        raise ValueError("Malformed sealed session.")
    now=int(time.time()) if now is None else int(now)
    if type(record["expires"]) is not int or record["expires"]<=now:
        raise PermissionError("Session expired; sign in again.")
    key=secret_key() if key is None else key
    try:
        plaintext=AESGCM(key).decrypt(bytes.fromhex(record["n"]),
                                       bytes.fromhex(record["c"]),host.encode())
        value=json.loads(plaintext)
    except (ValueError,TypeError,KeyError,InvalidTag,UnicodeError):
        raise ValueError("Session identity or integrity check failed.") from None
    if not isinstance(value,dict):
        raise ValueError("Unrecognised authenticated session.")
    if (value.get("host")!=host or value.get("expires")!=record["expires"]
        or type(value.get("issued")) is not int or
        not value["issued"]<=now or value["expires"]-value["issued"]>MAX_AGE):
        raise ValueError("Session identity/expiry mismatch.")
    _state(host,value["state"])
    return value["state"]

def list_sites(*,folder=HOME_DIR):
    root=_folder(folder)
    rows=[]
    for path in sorted(root.glob("*.session.enc"))[:200]:
        try:
            host=path.name[:-len(".session.enc")]
            _owned_file(path);valid_host(host)
            record=json.loads(path.read_bytes())
            rows.append({"site":host,"expires":record["expires"],
                         "expired":record["expires"]<=time.time()})
        except (ValueError,TypeError,OSError,PermissionError,KeyError):
            continue
    return rows

def revoke(host,*,folder=HOME_DIR,confirm=False):
    if confirm is not True:
        raise PermissionError("Owner confirmation required to revoke cached login.")
    path=_path(host,folder)
    if path.is_symlink():
        raise PermissionError("Symlinked session file.")
    path.unlink(missing_ok=True)
    return {"site":host,"local_cache_deleted":True,
            "notice":"For full revocation, sign out or revoke at the website too."}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="cmd",required=True)
    p=sub.add_parser("init");p.add_argument("--confirm",action="store_true")
    p=sub.add_parser("import");p.add_argument("site");p.add_argument("--state",type=Path,required=True)
    p.add_argument("--ttl-hours",type=int,default=24);p.add_argument("--confirm",action="store_true")
    sub.add_parser("list")
    p=sub.add_parser("revoke");p.add_argument("site");p.add_argument("--confirm",action="store_true")
    args=parser.parse_args(argv)
    try:
        if args.cmd=="init":
            secret_key(init=True,confirm=args.confirm)
            result={"key_location":"desktop_secret_service","initialised":True}
        elif args.cmd=="import":
            file=_owned_file(args.state)
            data=json.loads(file.read_bytes())
            result=save(args.site,data,ttl=args.ttl_hours*3600,confirm=args.confirm)
        elif args.cmd=="list":
            result=list_sites()
        else:
            result=revoke(args.site,confirm=args.confirm)
        print(json.dumps(result,indent=2))
        return 0
    except (OSError,RuntimeError,ValueError,TypeError,PermissionError,FileExistsError,KeyError) as err:
        print("Session vault refused: "+str(err)[:130])
        return 2

if __name__=="__main__":
    raise SystemExit(main())
