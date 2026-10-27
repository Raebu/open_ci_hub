#!/usr/bin/env python3
"""Owner-only, idempotent acceptance test for the Samsung ARM64 edge worker.

Executes locally on the Razer against its real Hub broker. Never logs API
credentials. A scoped submit credential is stored privately for future checks.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import time
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app_runtime import Broker

APP = "samsung-linux-edge"
TASK = "json.inspect"
TEXT = '{"device":"samsung-linux-edge","architecture":"arm64","checks":[1,2,3],"accepted":true}'
KEY = "samsung-linux-edge-acceptance-20261027-v1"
ROOT = Path(__file__).resolve().parent.parent
SECRET_DIR = ROOT / ".local" / "worker-credentials"
SECRET_FILE = SECRET_DIR / "samsung-linux-edge-submit.token"
EXPECTED = hashlib.sha256(json.dumps(json.loads(TEXT), sort_keys=True,
    separators=(",", ":"), allow_nan=False, ensure_ascii=True).encode()).hexdigest()


def submit_identity(broker: Broker):
    if SECRET_DIR.is_symlink() or SECRET_FILE.is_symlink():
        raise RuntimeError("Refusing symlink credential path")
    SECRET_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(SECRET_DIR, 0o700)
    if not SECRET_FILE.exists():
        issued = broker.issue(APP, "submit", confirm=True)
        token = issued["token"]
        fd = os.open(SECRET_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(token + "\n")
    if (SECRET_FILE.stat().st_mode & 0o777) != 0o600:
        raise RuntimeError("Credential permissions are not 0600")
    token = SECRET_FILE.read_text().strip()
    who = broker.auth(token)
    if who["app"] != APP or who["role"] != "submit":
        raise RuntimeError("Incorrect submit identity")
    return who


def main():
    broker = Broker()
    devices = [device for device in broker.edge_devices() if device["app"] == APP]
    if len(devices) != 1:
        raise RuntimeError("No unique approved Samsung Linux device")
    device = devices[0]
    if not device["approved"] or not device["online"] or device["kind"] != "linux_arm64":
        raise RuntimeError("Samsung worker not online/approved as Linux ARM64")
    who = submit_identity(broker)
    submitted = broker.submit(who, TASK, {"text": TEXT}, idempotency_key=KEY)
    job_id = submitted["job_id"]
    print(json.dumps({"submitted": job_id, "initial": submitted["status"],
                     "duplicate": submitted["duplicate"]}, sort_keys=True), flush=True)
    deadline = time.monotonic() + 140
    last = None
    while time.monotonic() < deadline:
        record = broker.inspect(who, job_id)
        if record["status"] != last:
            print(json.dumps({"job_id": job_id, "status": record["status"],
                              "attempts": record["attempts"]}, sort_keys=True), flush=True)
            last = record["status"]
        if record["status"] in ("completed", "failed", "stranded"):
            if record["status"] != "completed":
                raise RuntimeError("Test job did not complete: " + record["status"])
            result = record["result"]
            if not isinstance(result, dict) or result.get("valid") is not True:
                raise RuntimeError("Bad worker result: " + repr(result))
            if result.get("kind") != "dict" or result.get("sha256") != EXPECTED:
                raise RuntimeError("Worker result hash mismatch: " + repr(result))
            print(json.dumps({"acceptance": "PASSED", "job_id": job_id,
                              "task": TASK, "verified_hash": EXPECTED,
                              "attempts": record["attempts"]}, sort_keys=True))
            return
        time.sleep(3)
    raise RuntimeError("Timeout waiting for Samsung worker completion; job remains inspectable")


if __name__ == "__main__":
    main()
