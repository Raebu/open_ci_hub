#!/usr/bin/env python3
"""Local owner-controlled autonomy executor. Never a network-facing command API.

Only approved small fixed tasks and a fresh headless Chromium session execute.
Running a task does not grant permission to change external accounts or spend.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import tempfile
import uuid
import time

from autonomy_core import Journal, PolicyError, admit_local, sample_host, public_url, validate_plan
import ipaddress
import socket
import urllib.request

ROOT = Path(__file__).resolve().parent
DEFAULT_STATE = ROOT / ".local" / "autonomy"
BROWSER = ROOT / "integrations" / "background-browser" / "browser.mjs"

def execute_fixed(step):
    data = step["input"]
    if step["kind"] == "digest":
        content = data["text"].encode()
        return {"ok": True, "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content)}
    if step["kind"] == "inspect":
        try:
            value = json.loads(data["text"])
            canonical = json.dumps(value, sort_keys=True, separators=(",", ":"),
                                   allow_nan=False)
            return {"ok": True, "valid": True,
                    "sha256": hashlib.sha256(canonical.encode()).hexdigest()}
        except (ValueError, TypeError):
            return {"ok": True, "valid": False}
    raise PolicyError("Not a fixed task.")


class RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_readonly(url, allowlist):
    """Fast API/network read, no browser, no headers/cookies/credentials forwarded."""
    public_url(url, frozenset(allowlist))
    hostname = urllib.request.urlsplit(url).hostname
    try:
        candidates = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        if not candidates or any(
                not ipaddress.ip_address(item[4][0]).is_global for item in candidates):
            return {"ok": False, "error": "nonpublic_network_address"}
        opener = urllib.request.build_opener(RefuseRedirect())
        request = urllib.request.Request(url, method="GET", headers={
            "User-Agent": "OpenCIHub-Background/0.1"})
        with opener.open(request, timeout=8) as response:
            kind = response.headers.get_content_type()
            if kind not in ("text/html", "text/plain", "application/json"):
                return {"ok": False, "error": "unapproved_content_type"}
            data = response.read(65_537)
            if len(data) > 65_536:
                return {"ok": False, "error": "response_too_large"}
            return {"ok": True, "content_type": kind, "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "preview": data.decode("utf-8", "replace")[:1100]}
    except (OSError, ValueError, urllib.error.HTTPError):
        return {"ok": False, "error": "network_fetch_failed"}


def browser_job(plan, step, artifacts, *, node="node", timeout=45):
    """Browser process has NO connection to user's KDE/Wayland X11 session."""
    if step["kind"] != "browse":
        raise PolicyError("Not a browser step.")
    artifacts = Path(artifacts)
    artifacts.mkdir(parents=True, mode=0o700, exist_ok=False)
    if os.name == "posix":
        artifacts.chmod(0o700)
    payload = {"hosts": plan["hosts"], "url": step["input"]["url"],
               "actions": step["input"]["actions"],
               "allowInteraction": plan["limits"]["allow_browser_interaction"],
               "timeoutMs": min(30000, plan["limits"]["max_seconds"] * 1000)}
    # New empty HOME, temporary Chromium profile, and no display environment.
    with tempfile.TemporaryDirectory(prefix="oci-browser-home-") as temp:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": temp,
               "LANG": "C.UTF-8", "XDG_CACHE_HOME": temp,
               "OCI_CHROMIUM_PATH": "/usr/bin/chromium",
               "OCI_AUTONOMY_ARTIFACT_DIR": str(artifacts)}
        # A user transient service, NOT --scope --pipe (invalid on this system).
        # cgroup v2 must enforce RAM/CPU quotas before any browser job starts.
        unit = "open-ci-autonomy-" + uuid.uuid4().hex
        argv = ["/usr/bin/systemd-run", "--user", "--wait", "--pipe",
                "--quiet", "--collect", "--unit=" + unit,
                "-p", "MemoryMax=768M", "-p", "MemoryHigh=640M",
                "-p", "MemorySwapMax=0", "-p", "CPUQuota=60%",
                "-p", "IOWeight=15", "-p", "TasksMax=96",
                "-E", "HOME=" + temp,
                "-E", "XDG_CACHE_HOME=" + temp,
                "-E", "PATH=" + env["PATH"],
                "-E", "OCI_CHROMIUM_PATH=" + env["OCI_CHROMIUM_PATH"],
                "-E", "OCI_AUTONOMY_ARTIFACT_DIR=" + str(artifacts),
                "-E", "DISPLAY=", "-E", "WAYLAND_DISPLAY=",
                "--", "/usr/bin/node", str(BROWSER)]
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env=os.environ.copy(),
                                   cwd=str(ROOT), start_new_session=True,
                                   preexec_fn=_low_priority)
        try:
            stdout, _ = process.communicate(json.dumps(payload).encode(), timeout=timeout)
        except subprocess.TimeoutExpired:
            # Kill the entire browser group, not just Node, to avoid background
            # Chromium continuing to use CPU/RAM after a timeout.
            # Stop the cgroup service and wrapper; never assume a timed-out
            # wrapper means browser children stopped.
            subprocess.run(["/usr/bin/systemctl", "--user", "--no-ask-password",
                            "stop", unit + ".service"], timeout=8,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
            observed = subprocess.run(
                ["/usr/bin/systemctl", "--user", "show", unit + ".service",
                 "-p", "ActiveState", "-p", "ControlGroup"],
                capture_output=True, text=True, timeout=4)
            if ("ControlGroup=\n" not in observed.stdout or not any(
                    "ActiveState=" + state in observed.stdout
                    for state in ("inactive", "failed"))):
                raise RuntimeError("Timed out browser could not be proven stopped.")
            return {"ok": False, "error": "browser_timeout"}
        if len(stdout) > 8192:
            return {"ok": False, "error": "browser_output_too_large"}
        try:
            response = json.loads(stdout)
        except (ValueError, UnicodeError):
            return {"ok": False, "error": "browser_invalid_result"}
        if not isinstance(response, dict) or type(response.get("ok")) is not bool:
            return {"ok": False, "error": "browser_invalid_result"}
        return response


def _low_priority():
    os.nice(15)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (25, 26))


def run_one(journal, mission, *, sensor=sample_host, browser=browser_job):
    state = journal.snapshot(mission)
    if state["status"] == "needs_reconciliation":
        return {"status": "blocked", "reason": "previous_process_unverified"}
    # Interrupted steps can survive OS crashes; fail closed rather than replay.
    interrupted = journal.interrupted(mission)
    if interrupted:
        return {"status": "blocked", "reason": "running_step_needs_reconciliation",
                "steps": interrupted}
    task = journal.ready_step(mission)
    if task is None:
        return {"status": state["status"], "reason": "no_ready_step"}
    ok, reason = admit_local(sensor())
    if not ok:
        return {"status": "deferred", "reason": reason}
    journal.start_step(mission, task["id"])
    try:
        if task["kind"] == "browse":
            artifact = journal.folder / "artifacts" / mission / task["id"]
            # Parent itself is private; never allow arbitrary file destinations.
            artifact.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            result = browser(state["plan"], task, artifact,
                             timeout=min(state["plan"]["limits"]["max_seconds"], 45))
        elif task["kind"] == "fetch":
            result = fetch_readonly(task["input"]["url"], state["plan"]["hosts"])
        else:
            result = execute_fixed(task)
        receipt = journal.finish_step(mission, task["id"],
                                      ok=result["ok"], result=result)
        return {"status": receipt["status"], "step": task["id"],
                "evidence_sha256": receipt["evidence_sha256"]}
    except BaseException:
        # Do not mark failed if the process is unverified: a crash or KeyboardInterrupt
        # means any side effect may still be in progress.
        journal.interrupted(mission)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    sub = parser.add_subparsers(dest="cmd", required=True)
    submit = sub.add_parser("submit")
    submit.add_argument("plan", type=Path)
    submit.add_argument("--confirm", action="store_true")
    submit.add_argument("--confirm-interaction", action="store_true")
    run = sub.add_parser("run-once")
    run.add_argument("mission")
    status = sub.add_parser("status")
    status.add_argument("mission")
    cancel = sub.add_parser("cancel")
    cancel.add_argument("mission")
    cancel.add_argument("--confirm", action="store_true")
    sub.add_parser("host")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "host":
            return print(json.dumps({"host": sample_host(), "admission": admit_local(sample_host())})) or 0
        journal = Journal(args.state)
        if args.cmd == "submit":
            payload = args.plan.read_bytes()
            if len(payload) > 16384:
                raise PolicyError("Plan exceeds limit.")
            plan = validate_plan(json.loads(payload))
            if plan["limits"]["allow_browser_interaction"] and not args.confirm_interaction:
                raise PermissionError("Interaction needs a second explicit confirmation.")
            result = journal.submit(plan, owner_approved=args.confirm)
        elif args.cmd == "run-once":
            result = run_one(journal, args.mission)
        elif args.cmd == "status":
            result = journal.snapshot(args.mission)
        else:
            journal.cancel(args.mission, owner_approved=args.confirm)
            result = {"status": "cancelled"}
        print(json.dumps(result, indent=2))
        return 0
    except (PolicyError, PermissionError, RuntimeError, KeyError, OSError, ValueError) as exc:
        print("Autonomy: " + str(exc)[:180], file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
