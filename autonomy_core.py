"""Opt-in Open CI Hub autonomous missions: policy, leases, evidence and host safety.

This module does not execute arbitrary code or mutate a host. All work enters
through an owner-reviewed plan and separately scoped fixed-action executors.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import time
from urllib.parse import urlsplit

ID = re.compile(r"^[a-z][a-z0-9_-]{2,47}$")
HOST = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,63}$")
MAX_PLAN_BYTES = 16_384
SAFE_KINDS = frozenset({"browse", "digest", "inspect", "fetch"})
SAFE_BROWSER_ACTIONS = frozenset({"extract", "screenshot", "click", "fill"})
MUTATING_ACTIONS = frozenset({"click", "fill"})
MAX_STEPS = 20

class PolicyError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False)


def valid_host(value):
    if not isinstance(value, str) or len(value) > 253 or not HOST.fullmatch(value):
        raise PolicyError("Only explicitly named public DNS hosts are permitted.")
    if value.startswith("xn--") or ".xn--" in value or value.endswith(".local"):
        raise PolicyError("Ambiguous or local DNS name.")
    return value


def public_url(url, hosts):
    """Prevents trivially private and cross-origin URLs, including redirect targets.

    Network isolation must ALSO be enforced by the browser process or container:
    hostname allowlists alone are not a DNS-rebinding security boundary.
    """
    if not isinstance(url, str) or len(url) > 2048:
        raise PolicyError("Invalid URL.")
    u = urlsplit(url)
    if (u.scheme != "https" or not u.hostname or u.username or u.password
            or u.port not in (None, 443) or u.fragment or "\\" in url):
        raise PolicyError("HTTPS host-only URLs, no credentials or redirects to other origins.")
    host = valid_host(u.hostname)
    if host not in hosts:
        raise PolicyError("Host not in the mission allowlist.")
    return url


def validate_plan(plan):
    if not isinstance(plan, dict) or len(canonical(plan)) > MAX_PLAN_BYTES:
        raise PolicyError("Mission must be a small JSON object.")
    if set(plan) != {"id", "hosts", "steps", "limits"} or not ID.fullmatch(str(plan["id"])):
        raise PolicyError("Invalid mission identity or schema.")
    hosts = plan["hosts"]
    if not isinstance(hosts, list) or len(hosts) > 8 or len(hosts) != len(set(map(str, hosts))):
        raise PolicyError("Invalid origin allowlist.")
    hostset = frozenset(valid_host(host) for host in hosts)
    limits = plan["limits"]
    if (not isinstance(limits, dict)
            or set(limits) != {"max_cost_usd", "max_seconds", "allow_browser_interaction"}
            or type(limits["max_cost_usd"]) not in (int, float)
            or not 0 <= limits["max_cost_usd"] <= 100
            or type(limits["max_seconds"]) is not int
            or not 5 <= limits["max_seconds"] <= 180
            or type(limits["allow_browser_interaction"]) is not bool):
        raise PolicyError("Mission limits must be explicit.")
    # No backend with spending is implemented. Any positive-cost budget is
    # authorisation only, not evidence that a cloud provider has been enrolled.
    steps = plan["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise PolicyError("Mission requires 1–20 steps.")
    seen = set()
    for step in steps:
        if not isinstance(step, dict) or set(step) != {"id", "kind", "needs", "input"}:
            raise PolicyError("Invalid step schema.")
        sid, kind, needs, data = step["id"], step["kind"], step["needs"], step["input"]
        if not isinstance(sid, str) or not ID.fullmatch(sid) or sid in seen:
            raise PolicyError("Duplicate or invalid step.")
        if kind not in SAFE_KINDS or not isinstance(needs, list) or len(needs) != len(set(needs)):
            raise PolicyError("Unknown step kind/dependency.")
        if any(n not in seen for n in needs):
            raise PolicyError("Dependencies must precede the step (DAG).")
        if not isinstance(data, dict):
            raise PolicyError("Step input must be an object.")
        if kind == "browse":
            if set(data) != {"url", "actions"} or not hostset:
                raise PolicyError("Invalid browser step.")
            public_url(data["url"], hostset)
            actions = data["actions"]
            if not isinstance(actions, list) or not 1 <= len(actions) <= 12:
                raise PolicyError("Browser requires 1–12 actions.")
            for action in actions:
                if not isinstance(action, dict) or action.get("type") not in SAFE_BROWSER_ACTIONS:
                    raise PolicyError("Invalid browser action.")
                typ = action["type"]
                fields = {"type", "selector"} | ({"value"} if typ == "fill" else set())
                if set(action) != fields and not (typ == "screenshot" and set(action) == {"type"}):
                    raise PolicyError("Unexpected browser action arguments.")
                if typ != "screenshot":
                    sel = action.get("selector")
                    if not isinstance(sel, str) or not 1 <= len(sel) <= 150:
                        raise PolicyError("Bad selector.")
                if typ == "fill" and (not isinstance(action["value"], str)
                                      or len(action["value"]) > 240):
                    raise PolicyError("Oversized form value.")
                if typ in MUTATING_ACTIONS and not limits["allow_browser_interaction"]:
                    raise PolicyError("Browser interaction needs local owner approval.")
        elif kind == "fetch":
            if set(data) != {"url"} or not hostset:
                raise PolicyError("Fetch requires a validated public HTTPS URL.")
            public_url(data["url"], hostset)
        elif kind == "digest":
            if set(data) != {"text"} or not isinstance(data["text"], str) or len(data["text"]) > 2048:
                raise PolicyError("Digest task accepts only bounded text.")
        elif kind == "inspect":
            if set(data) != {"text"} or not isinstance(data["text"], str) or len(data["text"]) > 2048:
                raise PolicyError("Inspect task accepts only bounded JSON.")
        seen.add(sid)
    return plan


def sample_host():
    """Conservative foreground-first gate; never simulates additional RAM."""
    available = None
    try:
        match = re.search(r"^MemAvailable:\s+(\d+)\s+kB", Path("/proc/meminfo").read_text(), re.M)
        available = int(match.group(1)) // 1024 if match else None
    except (OSError, ValueError):
        pass
    temps = []
    for sensor in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
        try:
            value = int(sensor.read_text().strip()) / 1000
            if 0 <= value <= 110:
                temps.append(value)
        except (OSError, ValueError):
            pass
    try:
        load1 = os.getloadavg()[0] / max(1, os.cpu_count() or 1)
    except OSError:
        load1 = None
    return {"mem_available_mb": available, "load_per_core": load1,
            "max_thermal_c": max(temps) if temps else None}


def admit_local(host, *, max_temp=85, min_free_mb=4096):
    if not isinstance(host, dict):
        return False, "telemetry_missing"
    ram, load, temp = (host.get("mem_available_mb"),
                       host.get("load_per_core"), host.get("max_thermal_c"))
    if type(ram) is not int or ram < min_free_mb:
        return False, "memory_pressure_or_unknown"
    if type(load) not in (int, float) or not 0 <= load < 0.85:
        return False, "interactive_cpu_pressure"
    if temp is not None and (type(temp) not in (int, float) or temp >= max_temp):
        return False, "thermal_pressure"
    # Unknown sensors may not justify claiming thermal safety. Protect via
    # conservative single-job and low-priority process policy.
    return True, "quiet_capacity_available"


def choose_worker(workers, requirement):
    """Read-only decision. Remote execution needs independent authenticated transport."""
    if not isinstance(requirement, dict):
        raise PolicyError("Invalid job resource request.")
    arch, ram, kind = (requirement.get("architecture"), requirement.get("ram_mb"),
                       requirement.get("kind"))
    if arch not in ("arm64", "x86_64", "any") or kind not in SAFE_KINDS:
        raise PolicyError("Unsupported architecture or work.")
    if type(ram) is not int or not 64 <= ram <= 4096:
        raise PolicyError("Invalid RAM reservation.")
    candidates = []
    for worker in workers:
        if not isinstance(worker, dict):
            continue
        if (worker.get("online") is not True or worker.get("approved") is not True
            or kind not in worker.get("capabilities", [])
            or worker.get("architecture") not in ((arch, "any") if arch != "any" else ("arm64", "x86_64"))
            or type(worker.get("free_ram_mb")) is not int
            or worker["free_ram_mb"] < ram + 512
            or worker.get("busy") is True):
            continue
        cost = worker.get("cost_usd", None)
        if type(cost) not in (int, float) or cost != 0:
            continue  # Unknown costs fail closed; no cloud surprises.
        candidates.append(worker)
    candidates.sort(key=lambda w: (w.get("energy_rank", 100), -w["free_ram_mb"],
                                    w.get("id", "")))
    return candidates[0] if candidates else None


class Journal:
    """One-owner local state. Durable checkpoints, replay-safe step transitions."""
    def __init__(self, folder: Path):
        self.folder = Path(folder).absolute()
        if self.folder.is_symlink():
            raise PolicyError("Symlinked state forbidden.")
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(self.folder, 0o700)
        self.path = self.folder / "autonomy.sqlite3"
        if self.path.is_symlink():
            raise PolicyError("Symlinked database forbidden.")
        with self.connection() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS missions(
                id TEXT PRIMARY KEY, plan TEXT NOT NULL, status TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL
              );
              CREATE TABLE IF NOT EXISTS steps(
                mission TEXT NOT NULL, id TEXT NOT NULL, status TEXT NOT NULL,
                result TEXT, evidence_sha256 TEXT, updated REAL NOT NULL,
                PRIMARY KEY(mission,id), FOREIGN KEY(mission) REFERENCES missions(id)
              );
            """)
        if os.name == "posix":
            os.chmod(self.path, 0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, isolation_level=None, timeout=8)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            yield db
        finally:
            db.close()

    def submit(self, plan, *, owner_approved=False):
        validate_plan(plan)
        if owner_approved is not True:
            raise PermissionError("Local owner approval required.")
        now = time.time()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO missions VALUES(?,?,?,?,?)",
                       (plan["id"], canonical(plan), "queued", now, now))
            db.executemany("INSERT INTO steps(mission,id,status,updated) VALUES(?,?,?,?)",
                           [(plan["id"], step["id"], "queued", now) for step in plan["steps"]])
            db.commit()
        return {"mission": plan["id"], "status": "queued"}

    def snapshot(self, mission):
        with self.connection() as db:
            m = db.execute("SELECT * FROM missions WHERE id=?", (mission,)).fetchone()
            if not m:
                raise KeyError("Mission not found.")
            rows = db.execute("SELECT id,status,result,evidence_sha256 FROM steps "
                              "WHERE mission=? ORDER BY rowid", (mission,)).fetchall()
        return {"id": m["id"], "status": m["status"], "plan": json.loads(m["plan"]),
                "steps": [dict(row) for row in rows]}

    def ready_step(self, mission):
        s = self.snapshot(mission)
        if s["status"] in ("cancelled", "completed", "failed"):
            return None
        statuses = {r["id"]: r["status"] for r in s["steps"]}
        for step in s["plan"]["steps"]:
            if statuses[step["id"]] == "queued" and all(
                    statuses[dep] == "completed" for dep in step["needs"]):
                return step
        return None

    def start_step(self, mission, step):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            m = db.execute("SELECT status FROM missions WHERE id=?", (mission,)).fetchone()
            r = db.execute("SELECT status FROM steps WHERE mission=? AND id=?",
                           (mission, step)).fetchone()
            if not m or m["status"] not in ("queued", "running") or not r or r["status"] != "queued":
                raise RuntimeError("Job cannot start, possibly already leased.")
            db.execute("UPDATE steps SET status='running',updated=? WHERE mission=? AND id=?",
                       (time.time(), mission, step))
            db.execute("UPDATE missions SET status='running',updated=? WHERE id=?",
                       (time.time(), mission))
            db.commit()

    def finish_step(self, mission, step, *, ok, result):
        if type(ok) is not bool or not isinstance(result, dict):
            raise PolicyError("Invalid evidence.")
        wire = canonical(result)
        if len(wire) > 4096:
            raise PolicyError("Evidence too large.")
        proof = hashlib.sha256(wire.encode()).hexdigest()
        now = time.time()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM steps WHERE mission=? AND id=?",
                             (mission, step)).fetchone()
            if not row or row["status"] != "running":
                raise RuntimeError("No active step; cannot forge completion.")
            db.execute("UPDATE steps SET status=?,result=?,evidence_sha256=?,updated=? "
                       "WHERE mission=? AND id=?",
                       ("completed" if ok else "failed", wire, proof, now, mission, step))
            statuses = [row[0] for row in db.execute("SELECT status FROM steps WHERE mission=?",
                                                     (mission,))]
            overall = ("failed" if "failed" in statuses else
                       "completed" if all(x == "completed" for x in statuses) else "running")
            db.execute("UPDATE missions SET status=?,updated=? WHERE id=?",
                       (overall, now, mission))
            db.commit()
        return {"mission": mission, "step": step, "status": overall, "evidence_sha256": proof}

    def cancel(self, mission, *, owner_approved=False):
        if owner_approved is not True:
            raise PermissionError("Owner approval required.")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM missions WHERE id=?", (mission,)).fetchone()
            if not row or row["status"] not in ("queued", "running"):
                raise RuntimeError("Mission unavailable.")
            if db.execute("SELECT 1 FROM steps WHERE mission=? AND status='running'",
                          (mission,)).fetchone():
                raise RuntimeError("Running work must be stopped and verified first.")
            db.execute("UPDATE missions SET status='cancelled',updated=? WHERE id=?",
                       (time.time(), mission))
            db.commit()

    def interrupted(self, mission):
        """Never silently replay steps possibly continuing on another process."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            running = db.execute("SELECT id FROM steps WHERE mission=? AND status='running'",
                                 (mission,)).fetchall()
            if running:
                db.execute("UPDATE missions SET status='needs_reconciliation' WHERE id=?",
                           (mission,))
                db.commit()
            return [r["id"] for r in running]
