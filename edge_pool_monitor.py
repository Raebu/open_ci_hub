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
from pathlib import Path
import sys
from edge_pool import EdgePool
from edge_pool_cli import _payload_from_file

ROOT = Path(__file__).resolve().parent
PROJECTS_ROOT = ROOT.parent
LOCAL = ROOT / ".local"
DEFAULT_MANIFEST = LOCAL / "edge-pool-monitor.json"


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
        return {"manifest_missing":True, "dispatched":[], "skipped":[]}
    if manifest.stat().st_size>12000:
        raise ValueError("Manifest too large.")
    entries=json.loads(manifest.read_text("utf8"))
    if not isinstance(entries,list) or len(entries)>25:
        raise ValueError("Manifest requires up to 25 individual file entries.")
    dispatched=[]
    skipped=[]
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
            # Stable per-path and per-content: inspect once per change.
            key="monitor-"+hashlib.sha256((str(path)+"|"+task+"|"+fingerprint).encode()).hexdigest()
            receipt=pool.submit(task,payload,request_key=key,confirm=True)
            dispatched.append({"file":path.name,"device":receipt["device"],
                               "pool_job":receipt["pool_job"],
                               "duplicate":receipt["duplicate"]})
        except (ValueError, OSError, RuntimeError, PermissionError) as error:
            skipped.append({"file":Path(entry).name,"reason":str(error)})
    return {"manifest_missing":False,"dispatched":dispatched,"skipped":skipped}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest",default=str(DEFAULT_MANIFEST))
    parser.add_argument("--reconcile",action="store_true")
    args=parser.parse_args()
    pool=EdgePool()
    result=run(args.manifest,pool=pool)
    if args.reconcile:
        result["reconciliation"]=pool.reconcile(limit=40)
        result["audit"]=pool.audit()
    print(json.dumps(result,sort_keys=True))
    return 0


if __name__=="__main__":
    try:
        raise SystemExit(main())
    except (OSError,ValueError,RuntimeError) as error:
        print("Pool monitor: "+str(error),file=sys.stderr)
        raise SystemExit(1)
