#!/usr/bin/env python3
"""Trusted Razer operator interface to bounded S22/S25 job pool.

No browser-exposed token and no ambient project-file access on the phones.
Always requires --confirm for new work.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import secrets
import sys
import time

from edge_pool import EdgePool, ALLOWED, validate_payload, canonical

SENSITIVE = re.compile(
    r"(?i)(?:-----BEGIN .*PRIVATE KEY-----|"
    r"(?:api[_-]?key|secret|password|auth[_-]?token|bearer)['\"]?\s*[:=]\s*['\"]?\S+|"
    r"oci_v1_[A-Za-z0-9_-]{40,90})"
)
ALLOWED_EXTENSIONS = {".py":"python.syntax", ".json":"json.inspect"}


def _json_dump(value):
    print(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True), flush=True)


def _payload_from_file(path):
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ValueError("Input must be a regular, non-symlink source file.")
    if source.suffix not in ALLOWED_EXTENSIONS:
        raise ValueError("Only .py or .json files are supported.")
    if source.stat().st_size > 1500:
        raise ValueError("Only single-file source snippets <= 1500 bytes are supported.")
    text = source.read_text(encoding="utf-8")
    if SENSITIVE.search(text):
        raise ValueError("Potential credentials found; refusing to send this file to a phone.")
    task = ALLOWED_EXTENSIONS[source.suffix]
    payload = {"source":text} if task == "python.syntax" else {"text":text}
    validate_payload(task,payload)
    return task,payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action",required=True)
    sub.add_parser("devices", help="Show fresh eligible S22/S25 capacity.")
    sub.add_parser("report", help="Show pool receipts and tamper-evident log check.")
    sub.add_parser("audit",help="Verify local append-only hash chain.")
    rec=sub.add_parser("reconcile",help="Reassign only never-claimed offline work.")
    rec.add_argument("--limit",type=int,default=40)
    one=sub.add_parser("submit",help="Dispatch a bounded typed JSON payload.")
    one.add_argument("--task",choices=sorted(ALLOWED),required=True)
    one.add_argument("--payload",required=True,help="Small JSON object for the chosen fixed task.")
    one.add_argument("--key",help="Stable idempotency key; otherwise generated.")
    one.add_argument("--confirm",action="store_true",help="Owner approval for dispatch.")
    one.add_argument("--wait",type=int,default=0,help="Wait up to N seconds for receipt, <=180.")
    pre=sub.add_parser("check-file",help="Check an explicitly selected small JSON or Python file.")
    pre.add_argument("--file",required=True)
    pre.add_argument("--key")
    pre.add_argument("--confirm",action="store_true")
    pre.add_argument("--wait",type=int,default=0)
    batch=sub.add_parser("batch",help="Dispatch a list of 1..30 bounded jobs.")
    batch.add_argument("--file",required=True,help="JSON array [{task,payload,key},...].")
    batch.add_argument("--confirm",action="store_true")
    show=sub.add_parser("status",help="Inspect verified receipt from a prior pooled job.")
    show.add_argument("pool_job")
    args=parser.parse_args(argv)
    pool=EdgePool()
    if args.action=="devices":
        _json_dump(pool.devices_status())
    elif args.action=="report":
        _json_dump(pool.report())
    elif args.action=="audit":
        _json_dump(pool.audit())
    elif args.action=="reconcile":
        _json_dump(pool.reconcile(limit=args.limit))
    elif args.action=="status":
        _json_dump(pool.status(args.pool_job))
    elif args.action=="batch":
        if not args.confirm:
            raise PermissionError("Batch requires --confirm on the Razer.")
        path=Path(args.file)
        if path.is_symlink() or not path.is_file() or path.stat().st_size>50000:
            raise ValueError("Refusing unsafe batch file.")
        jobs=json.loads(path.read_text(encoding="utf8"))
        if not isinstance(jobs,list) or not 1<=len(jobs)<=30:
            raise ValueError("Batch requires 1 to 30 reviewed operations.")
        for job in jobs:
            if not isinstance(job,dict) or set(job)!={"task","payload","key"}:
                raise ValueError("Batch entries require exact task/payload/key fields.")
            validate_payload(job["task"],job["payload"])
        if len(set(x["key"] for x in jobs))!=len(jobs):
            raise ValueError("Duplicate idempotency keys in batch.")
        _json_dump([pool.submit(item["task"],item["payload"],
                               request_key=item["key"],confirm=True) for item in jobs])
    elif args.action in ("submit","check-file"):
        if args.action=="check-file":
            task,payload=_payload_from_file(args.file)
        else:
            task=args.task
            payload=json.loads(args.payload)
        if not args.confirm:
            raise PermissionError("Dispatch requires --confirm on the trusted Razer.")
        key=args.key or ("edgepool-"+secrets.token_hex(16))
        outcome=pool.submit(task,payload,request_key=key,confirm=True)
        _json_dump(outcome)
        if not 0<=args.wait<=180:
            raise ValueError("Wait must be 0..180 seconds.")
        deadline=time.monotonic()+args.wait
        while args.wait:
            record=pool.status(outcome["pool_job"])
            if record["status"] in ("completed","failed","stranded"):
                _json_dump(record)
                if record["verified"] is not True:
                    raise RuntimeError("Execution failed or result did not verify.")
                break
            if time.monotonic()>=deadline:
                _json_dump({"receipt":"pending","pool_job":outcome["pool_job"]})
                break
            time.sleep(3)
    return 0


if __name__=="__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError,ValueError,PermissionError,KeyError,OSError,json.JSONDecodeError) as error:
        print("Edge pool: "+str(error),file=sys.stderr)
        raise SystemExit(1)
