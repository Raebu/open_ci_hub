#!/usr/bin/env python3
"""Outbound provider change discovery for the existing Open CI Hub CI bridge.

Only an owner-owned 0600 manifest determines repositories and branches.
The first observation establishes a baseline; it does NOT schedule a build.
The shared app broker owns admission/leases and Docker worker execution.
"""
from __future__ import annotations
import argparse
import json
import sqlite3
import subprocess
import sys
import time

import ci_bridge as bridge

def ensure_cursors(broker):
    with broker._db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS ci_bridge_poll_cursors (
            provider TEXT NOT NULL, repo TEXT NOT NULL, branch TEXT NOT NULL,
            sha TEXT NOT NULL, observed_at REAL NOT NULL,
            PRIMARY KEY (provider, repo, branch)
        )""")

def latest_sha(provider, repo, branch):
    # Official provider CLIs use the owner's keyring. Avoid credential prompts.
    from urllib.parse import quote
    if provider == "github":
        argv=["gh","api",f"repos/{repo}/branches/{quote(branch,safe='')}"]
    elif provider == "gitlab":
        argv=["glab","api",f"projects/{quote(repo,safe='')}/repository/branches/{quote(branch,safe='')}"]
    else:
        raise ValueError("Unsupported provider")
    try:
        out=subprocess.run(argv,capture_output=True,text=True,timeout=35,
                           check=False,stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Remote branch check timed out") from None
    if out.returncode:
        raise RuntimeError("Provider CLI branch check failed; verify authentication")
    try:
        data=json.loads(out.stdout)
        sha=(data["commit"]["sha"] if provider=="github" else data["commit"]["id"])
    except (TypeError,KeyError,ValueError):
        raise RuntimeError("Unexpected provider branch response") from None
    if not isinstance(sha,str) or not bridge.SHA.fullmatch(sha):
        raise RuntimeError("Provider returned an invalid branch head")
    return sha

def poll_once(manifest=None, broker=None):
    manifest = manifest if manifest is not None else bridge.load_manifest()
    broker = bridge.ensure_scheduler(manifest, broker)
    ensure_cursors(broker)
    events = []
    for repo, cfg in sorted(manifest["repositories"].items()):
        provider = cfg["provider"]
        for branch in cfg["branches"]:
            try:
                sha = latest_sha(provider, repo, branch)
                with broker._db() as db:
                    row = db.execute(
                        "SELECT sha FROM ci_bridge_poll_cursors WHERE provider=? AND repo=? AND branch=?",
                        (provider, repo, branch),
                    ).fetchone()
                if row is None:
                    with broker._db() as db:
                        db.execute(
                            "INSERT OR IGNORE INTO ci_bridge_poll_cursors(provider,repo,branch,sha,observed_at) VALUES(?,?,?,?,?)",
                            (provider, repo, branch, sha, time.time()),
                        )
                    events.append({"repository":repo,"branch":branch,"result":"baseline","sha":sha})
                elif row["sha"] == sha:
                    events.append({"repository":repo,"branch":branch,"result":"unchanged","sha":sha})
                else:
                    # Queue first. Persist the cursor only after a successful queue write,
                    # so a temporary DB/provider failure cannot silently drop a change.
                    queued = bridge.enqueue(provider, repo, sha, branch, "push", manifest, broker)
                    with broker._db() as db:
                        db.execute(
                            "UPDATE ci_bridge_poll_cursors SET sha=?,observed_at=? WHERE provider=? AND repo=? AND branch=?",
                            (sha, time.time(), provider, repo, branch),
                        )
                    events.append({"repository":repo,"branch":branch,"result":"queued",
                                   "sha":sha,"job_id":queued["job_id"]})
            except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
                # Never log provider errors with embedded credential-bearing URLs.
                events.append({"repository":repo,"branch":branch,"result":"error",
                               "error":type(exc).__name__,"detail":str(exc)[:160]})
    return events

def main():
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest="command",required=True)
    sub.add_parser("once")
    poll=sub.add_parser("poll")
    poll.add_argument("--interval",type=int,default=120)
    args=parser.parse_args()
    if args.command=="poll" and not 60<=args.interval<=3600:
        parser.error("Polling interval must be 60–3600 seconds")
    try:
        manifest=bridge.load_manifest()
        broker=bridge.ensure_scheduler(manifest)
        while True:
            events=poll_once(manifest,broker)
            # Only log changes, first baselines and failures; no noisy unchanged logs.
            for event in events:
                if event["result"]!="unchanged":
                    print(json.dumps(event,sort_keys=True),flush=True)
            if args.command=="once":break
            time.sleep(args.interval)
        return 0 if not any(e["result"]=="error" for e in events) else 1
    except (OSError,PermissionError,RuntimeError,ValueError) as exc:
        print("CI polling error:",str(exc)[:200],file=sys.stderr)
        return 1
if __name__=="__main__":
    raise SystemExit(main())
