#!/usr/bin/env python3
"""Five-minute Razer fleet diagnosis, alongside existing edge pool timer.

Network connectivity does not imply a worker process is alive. Classifies
the real Galaxy Book Go/S22/S25 independently using the owner's approved
Hub broker and Tailscale's existing local status. No network scanning,
remote SSH retries, credential access, Android app control or DNS changes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from app_runtime import Broker

ROOT = Path(__file__).resolve().parent
STATUS_FILE = ROOT / ".local" / "fleet-connectivity.json"
SCHEMA = "oci.fleet-connectivity.v1"
# Explicitly trusted, user-owned tailnet IPs; do not guess a peer from
# a similar name. Future re-enrolment requires a reviewed mapping change.
DEVICES = (
    ("samsung-linux-edge", "Samsung Galaxy Book Go", "100.77.117.120",
     "samsung_stay_awake_and_worker_service"),
    ("s22-edge", "Samsung S22 Ultra", "100.95.64.110",
     "termux_worker_and_boot"),
    ("s25-edge", "Samsung S25", "100.123.89.45",
     "tailscale_termux_and_boot"),
)


def tailnet_online(*,run=subprocess.run):
    try:
        proc=run(["tailscale","status","--json"],capture_output=True,text=True,
                 check=False,timeout=7)
        if proc.returncode!=0 or len(proc.stdout)>220000:
            return None
        raw=json.loads(proc.stdout)
        if not isinstance(raw,dict) or not isinstance(raw.get("Peer"),dict):
            return None
        peers=raw["Peer"].values()
        return {ip:bool(peer.get("Online") is True)
                for peer in peers if isinstance(peer,dict)
                for ip in peer.get("TailscaleIPs",[])
                if isinstance(ip,str)}
    except (subprocess.TimeoutExpired,OSError,ValueError,TypeError):
        return None


def report(*,devices=None,peers=None,clock=time.time):
    raw=Broker().edge_devices() if devices is None else devices
    by_app={item["app"]:item for item in raw
            if isinstance(item,dict) and isinstance(item.get("app"),str)}
    peers=tailnet_online() if peers is None else peers
    now=clock()
    output=[]
    for app,label,ip,hint in DEVICES:
        worker=by_app.get(app)
        online=worker.get("online") is True if worker else False
        last_seen=worker.get("last_seen") if worker else None
        age=(round(max(0,now-last_seen)) if type(last_seen) in (float,int)
             else None)
        vpn=(peers.get(ip) if isinstance(peers,dict) else None)
        if vpn is None:
            state="vpn_state_unavailable"
            next_step="check_tailscale_identity_and_connectivity"
        elif vpn is False:
            state="device_or_vpn_offline"
            next_step="reconnect_device_and_tailscale"
        elif online and age is not None and age<=120:
            state="worker_ready"
            next_step="verify_real_test_task"
        elif worker and worker.get("approved") and age is not None and age<=120:
            state="worker_paused"
            next_step="check_power_memory_and_capacity"
        elif not worker or not worker.get("approved"):
            state="worker_not_approved"
            next_step="check_scoped_enrolment_without_credential_reset"
        else:
            state="vpn_online_worker_offline"
            next_step=hint
        output.append({
            "app":app,"label":label,"vpn_online":vpn,
            "worker_online":online and age is not None and age<=120,
            "worker_alive":age is not None and age<=120,
            "worker_eligible":online and age is not None and age<=120,"approved":bool(worker and worker.get("approved")),
            "last_worker_seen_age_seconds":age,"state":state,
            "next_step":next_step,
        })
    aggregate=("ready" if all(p["state"]=="worker_ready" for p in output)
               else "degraded" if any(p["state"] in (
                   "worker_ready","worker_paused","vpn_online_worker_offline") for p in output)
               else "offline_or_unknown")
    return {
        "schema":SCHEMA,
        "checked_at_utc":datetime.fromtimestamp(now,timezone.utc).isoformat(
            timespec="seconds"),
        "state":aggregate,"devices":output,
        "note":"Tailscale connection and Open CI Hub worker are distinct. "
               "No device was restarted remotely; Android Termux:Boot and "
               "Samsung Tailscale SSH require on-device/owner authorisation.",
        "side_effects":"none",
    }


def store(value,path=STATUS_FILE):
    path=Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("Refusing unsafe status destination.")
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    previous=None
    if path.is_file():
        try:
            previous=json.loads(path.read_text(encoding="utf8"))
        except (OSError,ValueError,TypeError):
            pass
    def state_signature(summary):
        return [(x["app"],x["state"],x["worker_online"],x["vpn_online"])
                for x in summary.get("devices",[]) if isinstance(x,dict)]
    value["material_change"]=(
        previous is None or state_signature(previous)!=state_signature(value))
    value["state_signature_sha256"]=hashlib.sha256(
        json.dumps(state_signature(value),sort_keys=True).encode()).hexdigest()
    body=json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False)
    if len(body)>12000:
        raise ValueError("Unbounded fleet snapshot refused.")
    fd,tmp=tempfile.mkstemp(prefix=".fleet-status-",dir=path.parent)
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
    return value


def cli(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=("check","status"),nargs="?",default="check")
    args=p.parse_args(argv)
    if args.action=="check":
        value=store(report())
    else:
        # Interactive reads use current telemetry; the timer snapshot is historical evidence.
        value=report()
        value["source"]="live"
    print(json.dumps(value,sort_keys=True,indent=2))
    return 0


if __name__=="__main__":
    try:
        raise SystemExit(cli())
    except (OSError,ValueError,TypeError) as exc:
        print("Fleet snapshot failed: "+type(exc).__name__)
        raise SystemExit(2)
