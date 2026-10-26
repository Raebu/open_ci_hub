#!/usr/bin/env python3
"""Single owner-facing entrypoint for approved, encrypted browser identities.

This deliberately does NOT extract Chrome passwords or give raw credentials to
agents. Unlocked keyring and approved exact-domain encrypted browser sessions
remain prerequisites. Browser automation and its permissions are unchanged.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import time
from browser_session_vault import (
    HOME_DIR, _owned_file, list_sites, load, revoke as forget_session, save,
    secret_key)
from browser_session_grants import (
    authorise, enabled, list_grants, revoke as deny)
from autonomy_core import valid_host

def overview(*,folder=HOME_DIR,now=None):
    now=int(time.time()) if now is None else int(now)
    records=list_sites(folder=folder)
    result=[]
    for entry in records:
        site=entry["site"]
        try:
            auto=enabled(site,folder=folder,now=now)
        except (OSError,RuntimeError,ValueError,PermissionError):
            auto=False
        result.append({"site":site,"encrypted":True,
                       "session_expired":entry["expired"],
                       "session_expires":entry["expires"],
                       "standing_read_only_access":bool(auto),
                       "reauthentication_required":bool(entry["expired"])})
    return {"provider":"existing_desktop_keyring_and_session_vault",
            "host_count":len(result),"sites":result,
            "raw_credentials_accessible_to_agents":False}

def check(host,*,folder=HOME_DIR,now=None):
    valid_host(host)
    now=int(time.time()) if now is None else int(now)
    try:
        active=enabled(host,folder=folder,now=now)
        if not active:
            return {"site":host,"available":False,
                    "reason":"no_standing_approval"}
        load(host,folder=folder,now=now)
        return {"site":host,"available":True,
                "scope":"read_only_exact_https_host"}
    except (OSError,PermissionError,RuntimeError,ValueError):
        return {"site":host,"available":False,
                "reason":"encrypted_session_unavailable_or_expired"}

def import_state(host,path,*,ttl_days=7,confirm=False,folder=HOME_DIR):
    if confirm is not True:
        raise PermissionError("Session import requires owner confirmation.")
    if type(ttl_days) is not int or not 1<=ttl_days<=7:
        raise ValueError("Encrypted session expiry must be 1–7 days.")
    incoming=_owned_file(Path(path))
    if incoming.stat().st_size>262144:
        raise ValueError("Unencrypted session too large.")
    data=json.loads(incoming.read_bytes())
    return save(host,data,folder=folder,ttl=ttl_days*86400,replace=True,
                confirm=True)

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="cmd",required=True)
    sub.add_parser("status")
    p=sub.add_parser("check");p.add_argument("host")
    p=sub.add_parser("approve");p.add_argument("host")
    p.add_argument("--days",type=int,default=90);p.add_argument("--confirm",action="store_true")
    p=sub.add_parser("pause");p.add_argument("host");p.add_argument("--confirm",action="store_true")
    p=sub.add_parser("forget");p.add_argument("host");p.add_argument("--confirm",action="store_true")
    p=sub.add_parser("import");p.add_argument("host");p.add_argument("--state",type=Path,required=True)
    p.add_argument("--ttl-days",type=int,default=7);p.add_argument("--confirm",action="store_true")
    args=parser.parse_args(argv)
    try:
        if args.cmd=="status":
            outcome=overview()
        elif args.cmd=="check":
            outcome=check(args.host)
        elif args.cmd=="approve":
            outcome=authorise(args.host,days=args.days,confirm=args.confirm)
        elif args.cmd=="pause":
            outcome=deny(args.host,confirm=args.confirm)
        elif args.cmd=="forget":
            if not args.confirm:
                raise PermissionError("Owner confirmation required.")
            # Revoke standing consent before deleting the session.
            deny(args.host,confirm=True)
            outcome=forget_session(args.host,confirm=True)
        elif args.cmd=="import":
            outcome=import_state(args.host,args.state,ttl_days=args.ttl_days,
                                 confirm=args.confirm)
        print(json.dumps(outcome,indent=2))
        return 0
    except (OSError,ValueError,TypeError,PermissionError,RuntimeError,FileExistsError) as exc:
        print("Authentication broker refused: "+str(exc)[:115])
        return 2

if __name__=="__main__":
    raise SystemExit(main())
