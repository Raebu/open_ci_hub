#!/usr/bin/env python3
"""Owner-curated lightweight project preflight checks across S22/S25.

Runs only against explicitly listed .json/.py files <=1500 bytes under the
approved Code_Projects tree. No auto-discovery or arbitrary code execution.
A stable file-content hash prevents repeated dispatch of unchanged files.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from edge_pool import EdgePool
from edge_pool_cli import _payload_from_file
from portable_edge_worker import task as evaluate_bounded

ROOT = Path(__file__).resolve().parent
PROJECTS_ROOT = ROOT.parent
LOCAL = ROOT / ".local"
DEFAULT_MANIFEST = LOCAL / "edge-pool-monitor.json"
STATUS_FILE = LOCAL / "edge-pool-status.json"


def safely_resolve(p, *, allowed):
    if any(segment.is_symlink() for segment in [p,*p.parents]):
        raise ValueError("Symlinks are forbidden in monitor paths.")
    resolved=p.resolve(strict=True)
    if not resolved.is_relative_to(allowed):
        raise ValueError("Monitor path outside approved Code_Projects root.")
    if ".local" in resolved.parts or ".git" in resolved.parts or ".ssh" in resolved.parts:
        raise ValueError("Private/config paths not eligible.")
    return resolved


def run(manifest=DEFAULT_MANIFEST, *, pool=None):
    pool=pool or EdgePool()
    manifest=Path(manifest)
    if manifest.is_symlink() or not manifest.is_file():
        return {"manifest_missing":True, "dispatched":[], "skipped":[], "deferred":[], "local_checks":[]}
    if manifest.stat().st_size>12000:
        raise ValueError("Manifest too large.")
    entries=json.loads(manifest.read_text("utf8"))
    if not isinstance(entries,list) or not 1<=len(entries)<=25:
        raise ValueError("Manifest requires up to 25 individual file entries.")
    dispatched=[]
    skipped=[]
    deferred=[]
    local_checks=[]
    for entry in entries:
        if not isinstance(entry,str):
            raise ValueError("Each monitor entry must be a single filename.")
        try:
            path=Path(entry)
            if not path.is_absolute():
                path=ROOT/path
            path=safely_resolve(path,allowed=PROJECTS_ROOT)
            task,payload=_payload_from_file(str(path))
            raw=path.read_bytes()
            fingerprint=hashlib.sha256(raw).hexdigest()
            # The same reviewed parser runs on Razer even if the phones
            # are offline; no source is imported or executed.
            local=evaluate_bounded(task,payload)
            local_ok=local.get("valid") is True
            local_checks.append({"file":path.name,"task":task,
                                 "valid":local_ok,"sha256":fingerprint})
            if not local_ok:
                skipped.append({"file":path.name,
                                "reason":"local_preflight_invalid_no_remote_dispatch"})
                continue
            # Stable per-path and per-content: inspect once per change.
            key="monitor-"+hashlib.sha256((str(path)+"|"+task+"|"+fingerprint).encode()).hexdigest()
            try:
                receipt=pool.submit(task,payload,request_key=key,confirm=True)
            except RuntimeError as exc:
                if "No available, fresh and eligible devices" in str(exc):
                    deferred.append({"file":path.name,"reason":"no_eligible_mobile_worker"})
                    continue
                raise
            dispatched.append({"file":path.name,"device":receipt["device"],
                               "pool_job":receipt["pool_job"],
                               "duplicate":receipt["duplicate"]})
        except (ValueError, OSError, RuntimeError, PermissionError) as error:
            skipped.append({"file":Path(entry).name,"reason":str(error)})
    return {"manifest_missing":False,"dispatched":dispatched,"skipped":skipped,
            "deferred":deferred,"local_checks":local_checks}


def build_status(result, pool):
    """Distinguish local quality, remote proof and device availability."""
    report=pool.report(limit=100)
    indexed={item["pool_job"]:item for item in report["jobs"]}
    receipts=[]
    issues=[]
    for item in result["dispatched"]:
        receipt=indexed.get(item["pool_job"]) or pool.status(item["pool_job"])
        receipts.append({"file":item["file"],"pool_job":item["pool_job"],
                         "device":receipt["device"],"status":receipt["status"],
                         "verified":receipt["verified"],
                         "check_passed":receipt["check_passed"]})
        if receipt["status"] in ("failed","stranded") or (
            receipt["status"]=="completed" and receipt["check_passed"] is not True
        ):
            issues.append({"file":item["file"],"kind":"remote_check_failed",
                           "pool_job":item["pool_job"]})
    for item in result["skipped"]:
        issues.append({"file":item["file"],"kind":"local_check_or_safety_failure",
                       "reason":item["reason"]})
    for item in result["deferred"]:
        issues.append({"file":item["file"],"kind":"waiting_for_phone"})
    devices=report["devices"]
    for device in devices:
        if not device["online"] or not device["eligible_for_json"]:
            issues.append({"kind":"worker_offline_or_unavailable",
                           "device":device["app"],
                           "last_seen_age_seconds":device["last_seen_age_seconds"]})
    if not any(d["eligible_for_json"] for d in devices):
        issues.append({"kind":"no_eligible_mobile_workers"})
    if not report["audit"]["valid"]:
        issues.append({"kind":"audit_chain_failure"})
    if result["manifest_missing"]:
        issues.append({"kind":"manifest_missing"})
    failures={"local_check_or_safety_failure","remote_check_failed",
              "audit_chain_failure","manifest_missing"}
    pending=sum(r["status"] in ("queued","leased") for r in receipts)
    status=("failed" if any(i["kind"] in failures for i in issues) else
            "degraded" if issues else "pending" if pending else "healthy")
    return {
        "last_run_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
        "state":status,
        "local_checks":{"total":len(result["local_checks"]),
                        "passed":sum(d["valid"] for d in result["local_checks"]),
                        "failed":sum(not d["valid"] for d in result["local_checks"])},
        "remote_checks":{"total":len(receipts),
                         "passed":sum(r["check_passed"] is True for r in receipts),
                         "pending":pending,"deferred":len(result["deferred"])},
        "worker_status":[{"app":d["app"],"online":d["online"],
                          "eligible":d["eligible_for_json"],
                          "last_seen_age_seconds":d["last_seen_age_seconds"]} for d in devices],
        "receipts":receipts,"issues":issues,
        "audit_valid":report["audit"]["valid"],
        "reconciliation":result.get("reconciliation",{}),
        "explanation":"Razer local parser runs even if phones are offline."
    }


def save_status(summary, filename=STATUS_FILE):
    """Atomically save private durable evidence without source or secrets."""
    path=Path(filename)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("Unsafe status report destination.")
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    body=json.dumps(summary,sort_keys=True,separators=(",",":"),allow_nan=False)
    fd,tmp=tempfile.mkstemp(prefix=".edge-pool-",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf8") as stream:
            stream.write(body+"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp,0o600)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest",default=str(DEFAULT_MANIFEST))
    parser.add_argument("--reconcile",action="store_true")
    args=parser.parse_args()
    pool=EdgePool()
    result=run(args.manifest,pool=pool)
    if args.reconcile:
        result["reconciliation"]=pool.reconcile(limit=40)
    summary=build_status(result,pool)
    save_status(summary)
    print(json.dumps({"state":summary["state"],
                      "local_checks":summary["local_checks"],
                      "remote_checks":summary["remote_checks"],
                      "worker_status":summary["worker_status"],
                      "issues":summary["issues"],
                      "audit_valid":summary["audit_valid"]},sort_keys=True))
    return 2 if summary["state"]=="failed" else 0


if __name__=="__main__":
    try:
        raise SystemExit(main())
    except (OSError,ValueError,RuntimeError) as error:
        print("Pool monitor: "+str(error),file=sys.stderr)
        raise SystemExit(1)
