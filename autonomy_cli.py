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
from autonomy_sandbox import SandboxUnavailable, browser_argv
import ipaddress
import socket

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


class PinnedHTTPSConnection:
    """Verified HTTPS/SNI to one already checked public address, no proxy or DNS replay."""
    def __init__(self, hostname, ip, *, timeout=8):
        import http.client
        import ssl
        self.host = hostname
        self.ip = ip
        self.ssl_context = ssl.create_default_context()
        self.connection = http.client.HTTPSConnection(hostname, timeout=timeout,
                                                      context=self.ssl_context)

    def request(self, path):
        import socket as network_socket
        # Pin the validated address while preserving TLS SNI and hostname checks.
        connection = self.connection
        raw = network_socket.create_connection((self.ip, 443), timeout=connection.timeout)
        try:
            connection.sock = self.ssl_context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise
        connection.request("GET", path, headers={
            "User-Agent": "OpenCIHub-Background/0.2",
            "Accept": "text/html, text/plain, application/json",
            "Accept-Encoding": "identity",
            "Connection": "close"})

    def getresponse(self):
        return self.connection.getresponse()

    def close(self):
        self.connection.close()


def fetch_readonly(url, allowlist):
    """Bounded API-first GET; public DNS is resolved once and TLS pins that IP."""
    from urllib.parse import urlsplit
    public_url(url, frozenset(allowlist))
    parsed = urlsplit(url)
    try:
        candidates = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        ips = [row[4][0] for row in candidates]
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            return {"ok": False, "error": "nonpublic_network_address"}
        # Use the resolved address without a second DNS query or user HTTP proxies.
        route = parsed.path or "/"
        if parsed.query:
            route += "?" + parsed.query
        connection = PinnedHTTPSConnection(parsed.hostname, ips[0])
        try:
            connection.request(route)
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                return {"ok": False, "error": "http_failure_or_redirect"}
            mime = response.headers.get_content_type()
            if mime not in ("text/html", "text/plain", "application/json"):
                return {"ok": False, "error": "unapproved_content_type"}
            payload = response.read(65537)
            if len(payload) > 65536:
                return {"ok": False, "error": "response_too_large"}
            return {"ok": True, "content_type": mime, "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "preview": payload.decode("utf-8", "replace")[:1100]}
        finally:
            connection.close()
    except (OSError, ValueError, TypeError):
        return {"ok": False, "error": "network_fetch_failed"}


def browser_job(plan, step, artifacts, *, node="node", timeout=45, session_host=None):
    """Browser process has NO connection to user's KDE/Wayland X11 session."""
    if step["kind"] != "browse":
        raise PolicyError("Not a browser step.")
    if session_host is not None:
        from urllib.parse import urlsplit
        from autonomy_core import valid_host
        valid_host(session_host)
        if (plan["hosts"]!=[session_host] or
            urlsplit(step["input"]["url"]).hostname!=session_host or
            plan["limits"]["allow_browser_interaction"] or
            any(a["type"] not in ("screenshot","extract")
                for a in step["input"]["actions"])):
            raise PermissionError("Cached login may only be used for a read-only single-site mission.")
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
        plan_path = Path(temp) / "plan.json"
        plan_path.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False),
                             encoding="utf-8")
        plan_path.chmod(0o600)
        session_file=None
        if session_host is not None:
            from browser_session_vault import load, _state
            session_file=Path(temp)/"session.json"
            fd=os.open(session_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            try:
                with os.fdopen(fd,"wb") as f:
                    f.write(_state(session_host,load(session_host)))
            except BaseException:
                session_file.unlink(missing_ok=True)
                raise
        sandbox = browser_argv(BROWSER.parent, artifacts, plan_file=plan_path,
                               session_file=session_file)
        argv = ["/usr/bin/systemd-run", "--user", "--wait", "--pipe",
                "--quiet", "--collect", "--unit=" + unit,
                "-p", "MemoryMax=768M", "-p", "MemoryHigh=720M",
                "-p", "MemorySwapMax=0", "-p", "CPUQuota=60%",
                "-p", "IOWeight=15", "-p", "TasksMax=384",
                "--", *sandbox]
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env=os.environ.copy(),
                                   cwd=str(ROOT), start_new_session=True,
                                   preexec_fn=_low_priority)
        try:
            stdout, _ = process.communicate(b"", timeout=timeout)
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


def run_one(journal, mission, *, sensor=sample_host, browser=browser_job,
            observer=None, session_host=None):
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
    ok, reason = admit_local(sensor(), min_free_mb=6144 if task["kind"] == "vision" else 4096)
    if not ok:
        return {"status": "deferred", "reason": reason}
    if session_host is not None:
        # Validate/decrypt before consuming a durable step lease. Wrong site
        # or locked desktop keyring must not become a failed mission step.
        from urllib.parse import urlsplit
        from browser_session_vault import load
        if (task["kind"] != "browse" or state["plan"]["hosts"] != [session_host]
            or urlsplit(task["input"]["url"]).hostname != session_host
            or state["plan"]["limits"]["allow_browser_interaction"]
            or any(a["type"] not in ("extract","screenshot")
                   for a in task["input"]["actions"])):
            raise PermissionError("Cached login requires a single-site read-only browser step.")
        load(session_host)
    journal.start_step(mission, task["id"])
    try:
        if task["kind"] == "browse":
            artifact = journal.folder / "artifacts" / mission / task["id"]
            # Parent itself is private; never allow arbitrary file destinations.
            artifact.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            options={"timeout":min(state["plan"]["limits"]["max_seconds"], 45)}
            if session_host is not None:
                options["session_host"]=session_host
            result = browser(state["plan"], task, artifact, **options)
        elif session_host is not None:
            raise PermissionError("Cached login cannot be applied to non-browser tasks.")
        elif task["kind"] == "fetch":
            result = fetch_readonly(task["input"]["url"], state["plan"]["hosts"])
        elif task["kind"] == "vision":
            from visual_observer import observe, screenshot_from_step
            source = task["input"]["source_step"]
            previous = next(row for row in state["steps"] if row["id"] == source)
            if previous["status"] != "completed":
                result = {"ok":False, "error":"source_not_completed"}
            else:
                evidence = json.loads(previous["result"])
                screenshot = screenshot_from_step(
                    journal.folder/"artifacts"/mission/source, evidence)
                result = (observer or observe)(
                    screenshot, task["input"]["question"])
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
    rehearsal = sub.add_parser("rehearse")
    rehearsal.add_argument("plan", type=Path)
    submit = sub.add_parser("submit")
    submit.add_argument("plan", type=Path)
    submit.add_argument("--confirm", action="store_true")
    submit.add_argument("--confirm-interaction", action="store_true")
    run = sub.add_parser("run-once")
    run.add_argument("mission")
    run.add_argument("--reuse-login",metavar="HOST",
                     help="Use exact-site encrypted browser session for this read-only step.")
    run.add_argument("--confirm-session",action="store_true")
    status = sub.add_parser("status")
    status.add_argument("mission")
    for action in ("pause", "resume", "cancel"):
        control = sub.add_parser(action)
        control.add_argument("mission")
        control.add_argument("--confirm", action="store_true")
    sub.add_parser("host")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "host":
            return print(json.dumps({"host": sample_host(), "admission": admit_local(sample_host())})) or 0
        if args.cmd == "rehearse":
            from mission_rehearsal import rehearse
            payload = args.plan.read_bytes()
            if len(payload) > 16384:
                raise PolicyError("Plan exceeds limit.")
            print(json.dumps(rehearse(json.loads(payload)), indent=2))
            return 0
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
            if args.reuse_login and not args.confirm_session:
                raise PermissionError("Reusable authenticated browser requires --confirm-session.")
            result = run_one(journal, args.mission,session_host=args.reuse_login)
        elif args.cmd == "status":
            result = journal.snapshot(args.mission)
        else:
            if args.cmd not in ("pause", "resume", "cancel"):
                raise PolicyError("Unsupported local command.")
            op = getattr(journal, args.cmd)
            result = op(args.mission, owner_approved=args.confirm)
            if args.cmd == "cancel":
                result = {"mission": args.mission, "status": "cancelled"}
        print(json.dumps(result, indent=2))
        return 0
    except (PolicyError, PermissionError, RuntimeError, KeyError, OSError, ValueError) as exc:
        print("Autonomy: " + str(exc)[:180], file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
