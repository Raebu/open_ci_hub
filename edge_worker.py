#!/usr/bin/env python3
"""Opt-in, non-root Android/Termux worker for the existing Open CI Hub job API.

Built-in bounded data operations only: no shell jobs, source execution,
downloads, personal files, contact access, root or Docker.
"""
from __future__ import annotations
import argparse
import ast
from functools import partial
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

TASKS = frozenset(("python.syntax", "json.inspect", "hash.benchmark"))
MAX_TEXT_BYTES = 1500
MAX_RAM_MB = 512

def termux_sensor(binary):
    if binary not in ("termux-battery-status", "termux-wifi-connectioninfo"):
        raise ValueError("Unapproved sensor")
    try:
        p = subprocess.run([binary], capture_output=True, timeout=5, check=False)
        if p.returncode or len(p.stdout) > 4096:
            return None
        value = json.loads(p.stdout)
        return value if isinstance(value, dict) else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None

def eligible(battery, wifi):
    if not isinstance(battery, dict) or not isinstance(wifi, dict):
        return False, "sensor_unavailable"
    level = battery.get("percentage")
    try:
        temp = float(battery.get("temperature"))
    except (ValueError, TypeError, OverflowError):
        return False, "temperature_unavailable"
    if (type(level) is not int or not 0 <= level <= 100
            or not math.isfinite(temp) or not -10 <= temp <= 75):
        return False, "sensor_invalid"
    if battery.get("health") != "GOOD":
        return False, "battery_health"
    if level < 70:
        return False, "battery_low"
    if temp >= 40:
        return False, "battery_warm"
    if battery.get("plugged") not in (
            "PLUGGED_AC", "PLUGGED_USB", "PLUGGED_WIRELESS", "PLUGGED_DOCK"):
        return False, "not_charging"
    if wifi.get("supplicant_state") != "COMPLETED" or wifi.get("ip") in (
            None, "", "0.0.0.0"):
        return False, "wifi_unavailable"
    return True, "eligible"

def phone_ready():
    return eligible(termux_sensor("termux-battery-status"),
                    termux_sensor("termux-wifi-connectioninfo"))

def get_text(payload, key):
    if not isinstance(payload, dict):
        raise ValueError("Invalid payload")
    value = payload.get(key)
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValueError("Invalid text")
    return value

def fixed_task(task, payload):
    """Pure dispatch: ast.parse is not Python execution."""
    if task == "python.syntax":
        source = get_text(payload, "source")
        sha = hashlib.sha256(source.encode()).hexdigest()
        try:
            tree = ast.parse(source)
            return {"valid": True, "nodes": sum(1 for _ in ast.walk(tree)), "sha256": sha}
        except SyntaxError as exc:
            return {"valid": False, "line": exc.lineno, "sha256": sha}
    if task == "json.inspect":
        source = get_text(payload, "text")
        try:
            value = json.loads(source)
        except (ValueError, UnicodeError):
            return {"valid": False}
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=True, allow_nan=False)
        return {"valid": True, "kind": type(value).__name__,
                "sha256": hashlib.sha256(canonical.encode()).hexdigest()}
    if task == "hash.benchmark":
        source = get_text(payload, "text")
        count = payload.get("iterations")
        if type(count) is not int or not 1000 <= count <= 50000:
            raise ValueError("Bad iteration count")
        digest = hashlib.pbkdf2_hmac("sha256", source.encode(),
                                     b"open-ci-edge-benchmark-v1", count)
        return {"sha256": digest.hex(), "iterations": count}
    raise ValueError("Unapproved task")

def child_main(task):
    try:
        wire = sys.stdin.buffer.read(2049)
        if len(wire) > 2048:
            raise ValueError("Oversized request")
        result = fixed_task(task, json.loads(wire))
        outcome = {"success": True, "result": result}
    except (ValueError, TypeError, OverflowError, RecursionError):
        outcome = {"success": False, "result": {"error": "invalid_payload"}}
    print(json.dumps(outcome, separators=(",", ":"), allow_nan=False))
    return 0

def enforce_limits(ram_mb):
    maximum = ram_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (maximum, maximum))
    resource.setrlimit(resource.RLIMIT_CPU, (10, 11))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

def execute(task, payload, ram_mb=MAX_RAM_MB):
    if type(ram_mb) is not int or not 64 <= ram_mb <= MAX_RAM_MB:
        return False, {"error": "unsupported_resource_profile"}
    if task not in TASKS:
        return False, {"error": "task_not_approved"}
    if not isinstance(payload, (dict, list)):
        return False, {"error": "invalid_payload"}
    try:
        wire = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
        if len(wire) > 2048:
            return False, {"error": "invalid_payload"}
        # Scrub worker credentials from the child and disable Python user-site imports.
        clean_env = {"HOME": str(Path.home()), "LANG": "C.UTF-8",
                     "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        p = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()),
                            "_task", task], input=wire, capture_output=True,
                           timeout=15, check=False, env=clean_env,
                           preexec_fn=partial(enforce_limits, ram_mb))
        if p.returncode or len(p.stdout) > 2048:
            return False, {"error": "task_execution_failed"}
        response = json.loads(p.stdout)
        if (not isinstance(response, dict)
                or type(response.get("success")) is not bool
                or not isinstance(response.get("result"), dict)):
            raise ValueError("Malformed child response")
        return response["success"], response["result"]
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return False, {"error": "task_execution_failed"}

def complete_claim(client, job):
    if (not isinstance(job, dict) or not isinstance(job.get("job_id"), str)
            or not isinstance(job.get("lease_token"), str)):
        raise ValueError("Malformed lease")
    budget = job.get("resources")
    if (not isinstance(budget, dict)
            or type(budget.get("ram_mb")) is not int
            or not 64 <= budget["ram_mb"] <= MAX_RAM_MB
            or budget.get("vram_mb") != 0):
        success, result = False, {"error": "unsupported_resource_profile"}
    elif not phone_ready()[0]:
        success, result = False, {"error": "device_safety_gate"}
    else:
        success, result = execute(job.get("task"), job.get("payload"), budget["ram_mb"])
    return client.complete(job["job_id"], job["lease_token"], success, result)

def once(client):
    ok, reason = phone_ready()
    if not ok:
        return {"status": "skipped", "reason": reason}
    # Optional modern device enrollment. Each Android phone has its own
    # scoped app token, not an unrestricted GitHub runner credential.
    if hasattr(client, "availability"):
        left=None
        try:
            from pathlib import Path
            for item in Path("/proc/meminfo").read_text().splitlines():
                if item.startswith("MemAvailable:"):
                    left=int(item.split()[1])//1024
                    break
        except (OSError,ValueError):
            pass
        if type(left) is not int:
            return {"status":"skipped","reason":"memory_telemetry_unavailable"}
        presence=client.availability("Android","arm64",left,True)
        if not isinstance(presence,dict) or presence.get("enrolled") is not True:
            return {"status":"skipped","reason":"owner_enrolment_required"}
        if presence.get("online") is not True:
            return {"status":"skipped","reason":"device_capacity_gate"}
    claim = client.claim()
    if not isinstance(claim, dict):
        raise ValueError("Invalid Hub claim response")
    if claim.get("job") is None:
        return {"status": "idle"}
    job = claim["job"]
    response = complete_claim(client, job)
    return {"status": "acknowledged", "job_id": job["job_id"],
            "hub_status": response.get("status")}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=45)
    parser.add_argument("internal", nargs="?")
    parser.add_argument("task", nargs="?")
    args = parser.parse_args(argv)
    if args.internal == "_task":
        return child_main(args.task)
    if args.internal:
        parser.error("Invalid mode")
    if not 15 <= args.interval <= 600:
        parser.error("Interval must be 15..600")
    if args.dry_run:
        ok, reason = phone_ready()
        print(json.dumps({"eligible": ok, "reason": reason}))
        return 0 if ok else 1
    # The same private application token and HTTPS Tailnet restrictions as the Hub SDK.
    from application_client import ApplicationClient
    try:
        client = ApplicationClient(os.environ.get("OCI_EDGE_URL", ""),
                                   os.environ.get("OCI_EDGE_WORKER_TOKEN", ""))
    except ValueError:
        print("Worker configuration invalid.", file=sys.stderr)
        return 2
    try:
        while True:
            try:
                print(json.dumps(once(client), separators=(",", ":")), flush=True)
            except (OSError, ValueError, RuntimeError):
                print('{"status":"error","reason":"hub_unavailable"}', flush=True)
            if args.once:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0

if __name__ == "__main__":
    raise SystemExit(main())
