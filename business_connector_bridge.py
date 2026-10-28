"""Bounded, privacy-safe capability probe from already connected ChatGPT apps.

This handoff is evidence of a connected app call, not proof of a local OAuth
credential and never an instruction to send, scrape, or message LinkedIn.
The canonical inbox, suppression source and sales tracker remain unchanged.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time

from business_channels import ROOT_STATE, ChannelError

SCHEMA="raeburn.chat-connector-capabilities.v1"
FILE=ROOT_STATE/"connector-capabilities.json"
ALLOWED={"raeburn-primary":"gmail","raeburn-contact":"gmail",
         "raeburn-resend":"resend","linkedin-profile-only":"linkedin"}
MAX_AGE=7200
STATUS={"connected_read_probe","read_probe_failed",
        "profile_lookup_only","not_authorised"}


def validate(payload,*,now=None):
    now=time.time() if now is None else now
    if not isinstance(payload,dict) or set(payload)!={
        "schema","observed_at","entries"} or payload["schema"]!=SCHEMA:
        raise ChannelError("Invalid connected-app probe envelope.")
    at=payload["observed_at"]
    if not isinstance(at,str):
        raise ChannelError("Missing source-probe timestamp.")
    try:
        stamp=datetime.fromisoformat(at.replace("Z","+00:00"))
        if stamp.tzinfo is None:
            raise ValueError
        age=now-stamp.timestamp()
    except (ValueError,OverflowError):
        raise ChannelError("Invalid source-probe timestamp.") from None
    if not 0<=age<=MAX_AGE:
        raise ChannelError("Source probe stale or dated in the future.")
    entries=payload["entries"]
    if not isinstance(entries,list) or not 1<=len(entries)<=4:
        raise ChannelError("Bounded business provider entries required.")
    seen=set()
    for x in entries:
        if not isinstance(x,dict) or set(x)!={"alias","provider","state",
                                             "bounded_result_count","more_available"}:
            raise ChannelError("Only non-sensitive numeric capability fields permitted.")
        if x["alias"] in seen or ALLOWED.get(x["alias"])!=x["provider"]:
            raise ChannelError("Unexpected provider or duplicate alias.")
        seen.add(x["alias"])
        if x["state"] not in STATUS or (
            x["provider"]=="linkedin" and x["state"]=="connected_read_probe"
        ):
            raise ChannelError("Unsupported provider capability claim.")
        if type(x["bounded_result_count"]) is not int or not 0<=x[
                "bounded_result_count"]<=100 or type(x["more_available"]) is not bool:
            raise ChannelError("Unbounded or malformed readback count.")
    return {"observed_at":at,"entries":entries,"age_seconds":round(age),
            "native_api_credentials_verified":False,
            "outbound_messaging_access":False,
            "independent_continuous_sync":False}


def store(payload,path=FILE):
    value=validate(payload)
    path=Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise ChannelError("Symlinked capability snapshot refused.")
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    data=json.dumps(payload,separators=(",",":"),sort_keys=True)
    fd,temp=tempfile.mkstemp(dir=path.parent,prefix=".conn-probe-")
    try:
        with os.fdopen(fd,"w",encoding="utf8") as f:
            f.write(data+"\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp,0o600)
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return {"recorded":len(value["entries"]),
            "independent_continuous_sync":False}


def load(path=FILE,*,now=None):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>6000:
        return {"state":"not_recorded","native_api_credentials_verified":False}
    if os.name=="posix" and (path.stat().st_mode&0o077):
        return {"state":"unsafe_file","native_api_credentials_verified":False}
    try:
        report=validate(json.loads(path.read_text("utf8")),now=now)
        return {"state":"connected_app_probe_fresh",**report}
    except (ChannelError,OSError,TypeError):
        return {"state":"stale_or_invalid","native_api_credentials_verified":False}


def main(argv=None):
    import argparse,sys
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=("read","import"))
    args=p.parse_args(argv)
    try:
        if args.action=="read":
            result=load()
        else:
            raw=sys.stdin.buffer.read(6001)
            if len(raw)>6000:
                raise ChannelError("Capability probe too large.")
            result=store(json.loads(raw))
        print(json.dumps(result,sort_keys=True))
        return 0
    except (ChannelError,OSError,ValueError) as exc:
        print("Connector capability import refused: "+str(exc)[:160],
              file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
