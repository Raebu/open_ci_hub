#!/usr/bin/env python3
"""Fixed, restricted compute tasks for an EXISTING owner-approved Contabo VPS.

No Git checkout, shell, Docker socket, cloud provisioning, prompt execution,
paid-provider API, or ability to read the host's production service credentials.
This is an on-demand SSH dispatch path, not a connected CI queue worker.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import os
import subprocess
import sys

LIMIT=2048
TASKS=frozenset(("json.inspect","python.syntax","hash.benchmark"))
REMOTE_PATH="/usr/local/lib/open-ci-hub/contabo_compute.py"
TARGET="contabo-talent"

def execute(request):
    if type(request) is not dict or set(request)!={"task","payload"}:
        raise ValueError("Invalid typed work envelope.")
    kind=request["task"]
    payload=request["payload"]
    if kind not in TASKS or type(payload) is not dict:
        raise ValueError("Unapproved work.")
    if kind=="json.inspect":
        if set(payload)!={"text"} or not isinstance(payload["text"],str) or len(payload["text"].encode())>1500:
            raise ValueError("Invalid JSON input.")
        value=json.loads(payload["text"])
        wire=json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode()
        return {"kind":type(value).__name__,"sha256":hashlib.sha256(wire).hexdigest(),"valid":True}
    if kind=="python.syntax":
        if set(payload)!={"source"} or not isinstance(payload["source"],str) or len(payload["source"].encode())>1500:
            raise ValueError("Invalid source length.")
        source=payload["source"]
        try:
            tree=ast.parse(source)
        except SyntaxError:
            return {"valid":False,"sha256":hashlib.sha256(source.encode()).hexdigest()}
        return {"valid":True,"nodes":sum(1 for _ in ast.walk(tree)),
                "sha256":hashlib.sha256(source.encode()).hexdigest()}
    if set(payload)!={"text","iterations"} or not isinstance(payload["text"],str) or len(payload["text"].encode())>1500:
        raise ValueError("Invalid benchmark.")
    iterations=payload["iterations"]
    if type(iterations) is not int or not 1000<=iterations<=50000:
        raise ValueError("Invalid iteration count.")
    return {"iterations":iterations,
            "sha256":hashlib.pbkdf2_hmac("sha256",payload["text"].encode(),
                                         b"open-ci-contabo-bounded-v1",iterations).hex()}

def worker():
    data=sys.stdin.buffer.read(LIMIT+1)
    if len(data)>LIMIT:
        raise ValueError("Request too large.")
    try:
        request=json.loads(data)
        result=execute(request)
        print(json.dumps({"success":True,"result":result},separators=(",",":"),allow_nan=False))
        return 0
    except (ValueError,TypeError,OverflowError,RecursionError):
        print('{"success":false,"result":{"error":"invalid_or_unapproved_task"}}')
        return 2

def remote_argv():
    # SSH uses an explicitly configured owner-known host; systemd-run launches
    # the child as a fresh transient dynamic UID without root or network.
    return [
        "ssh","-T","-o","BatchMode=yes","-o","ConnectTimeout=7",
        "-o","StrictHostKeyChecking=yes",TARGET,
        "systemd-run","--quiet","--wait","--pipe","--collect",
        "--property=DynamicUser=yes",
        "--property=PrivateNetwork=yes",
        "--property=PrivateTmp=yes",
        "--property=ProtectSystem=strict",
        "--property=ProtectHome=yes",
        "--property=NoNewPrivileges=yes",
        "--property=CapabilityBoundingSet=",
        "--property=RestrictAddressFamilies=AF_UNIX",
        "--property=RestrictSUIDSGID=yes",
        "--property=ProtectKernelTunables=yes",
        "--property=ProtectControlGroups=yes",
        "--property=ProtectKernelModules=yes",
        "--property=ProtectKernelLogs=yes",
        "--property=MemoryMax=256M",
        "--property=CPUQuota=50%",
        "--property=TasksMax=16",
        "--property=RuntimeMaxSec=30s",
        "--","/usr/bin/python3","-I",REMOTE_PATH,"--worker"
    ]

def dispatch(request):
    # Only exact typed payloads are accepted, with bounded network and child
    # resource usage; SSH credentials do not enter the worker environment.
    execute(request)
    wire=json.dumps(request,separators=(",",":"),allow_nan=False).encode()
    if len(wire)>LIMIT:
        raise ValueError("Request too large.")
    p=subprocess.run(remote_argv(),input=wire,capture_output=True,timeout=45,
                     check=False,env={**os.environ,"GIT_TERMINAL_PROMPT":"0"})
    if p.returncode:
        raise RuntimeError("Restricted remote execution was unavailable; no retry or escalation.")
    try:
        output=json.loads(p.stdout)
    except (ValueError,UnicodeError):
        raise RuntimeError("Remote worker produced no verified JSON receipt.") from None
    if (type(output) is not dict or output.get("success") is not True
            or type(output.get("result")) is not dict):
        raise RuntimeError("Remote worker refused the work.")
    local=execute(request)
    if output["result"]!=local:
        raise RuntimeError("Remote proof mismatches expected deterministic task.")
    return {"target":"contabo-existing-vps","task":request["task"],
            "verified":True,"result":output["result"],
            "isolation":"dynamic_unprivileged_user_private_network_and_memory_limit"}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--worker",action="store_true")
    group.add_argument("--smoke",action="store_true")
    group.add_argument("--task",choices=sorted(TASKS))
    args=parser.parse_args()
    if args.worker:
        return worker()
    if args.smoke:
        request={"task":"json.inspect","payload":{"text":'{"open_ci_hub":"contabo","mode":"bounded"}'}}
    else:
        # No command-line user data. Only bounded input on stdin.
        raw=sys.stdin.buffer.read(LIMIT+1)
        if len(raw)>LIMIT:raise SystemExit("Request exceeds cap.")
        request=json.loads(raw)
        if request.get("task")!=args.task:
            raise SystemExit("Task identity mismatch.")
    print(json.dumps(dispatch(request),sort_keys=True))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
