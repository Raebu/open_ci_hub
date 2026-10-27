#!/usr/bin/env python3
"""One-at-a-time low-impact background mission loop.

Only processes existing locally approved plans. Does not create missions,
grant scopes, register cloud machines or bypass the foreground resource gate.
"""
from __future__ import annotations
import argparse
import fcntl
import json
from pathlib import Path
import signal
import sys
import time

from autonomy_cli import DEFAULT_STATE, run_one
from autonomy_core import Journal

stop = False

def _stop(*_args):
    global stop
    stop = True

def candidates(journal):
    with journal.connection() as db:
        rows = db.execute(
            "SELECT id FROM missions WHERE status IN ('queued','running') "
            "ORDER BY created LIMIT 30").fetchall()
    return [row["id"] for row in rows]

def tick(journal, *, runner=run_one):
    for mission in candidates(journal):
        output = runner(journal, mission)
        if output.get("status") not in ("queued", "deferred"):
            return {"mission": mission, "status": output.get("status"),
                    "step": output.get("step"), "reason": output.get("reason")}
        if output.get("status") == "deferred":
            return {"status": "deferred", "reason": output.get("reason")}
    return {"status": "idle"}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if not 30 <= args.interval <= 600:
        parser.error("Interval must be 30–600 seconds.")
    journal = Journal(args.state)
    # Shared application broker: existing Android workers only, no extra daemon.
    from app_runtime import Broker
    app_broker=Broker()
    lock = journal.folder / "autonomy-daemon.lock"
    # A separate process on the same host must not run the same mission queue.
    with lock.open("a+") as fd:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('{"status":"already_running"}')
            return 1
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        while not stop:
            try:
                result = tick(journal)
                # Pure, idempotency-keyed Android jobs only. Other stranded
                # leases remain blocked pending original-worker evidence.
                edge=app_broker.reconcile_pure_edge_stranded()
                if edge["requeued_pure_edge_jobs"] or edge["finalised_pure_edge_jobs"]:
                    print(json.dumps({"status":"pure_edge_recovery",
                                      "requeued":len(edge["requeued_pure_edge_jobs"]),
                                      "finalised":len(edge["finalised_pure_edge_jobs"])}),
                          flush=True)
                # No URLs, job data, result text or secrets in daemon logs.
                print(json.dumps(result, separators=(",", ":")), flush=True)
            except Exception:
                print('{"status":"error","reason":"job_unverified"}', flush=True)
            if args.once:
                break
            for _ in range(args.interval):
                if stop:
                    break
                time.sleep(1)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
