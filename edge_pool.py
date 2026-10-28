#!/usr/bin/env python3
"""Owner-local, auditable scheduler for approved Open CI Hub edge devices.

Only dispatches existing, strictly bounded operations through the *existing*
per-device broker queues. No remote shell, arbitrary execution, secrets,
filesystem access, provider cloud billing, or cross-device leased-job replay.
"""
from __future__ import annotations

import functools
import hashlib
import json
import re
import secrets
import sqlite3
import time

from app_runtime import Broker, IDEMPOTENCY, MAX_PENDING, MAX_TOTAL
from resource_guard import ResourceGate
import fleet_devices
from portable_edge_worker import task as independently_check_task

DEFAULT_DEVICES = ("s22-edge", "s25-edge")
ALLOWED = frozenset(("python.syntax", "json.inspect", "hash.benchmark"))
JOB_ID = re.compile(r"^[a-f0-9]{32}$")
MAX_MOVES = 2
MIN_RAM_HEADROOM_MB = 1024
REQUIRED_RAM_MB = 1536
RECEIPT_VERSION = 1


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def validate_payload(task, payload):
    if task not in ALLOWED or not isinstance(payload, dict):
        raise ValueError("Only reviewed portable edge tasks are allowed.")
    required = {"python.syntax": {"source"}, "json.inspect": {"text"},
                "hash.benchmark": {"text", "iterations"}}[task]
    if set(payload) != required:
        raise ValueError("Unexpected payload keys or missing required fields.")
    text = payload["source"] if task == "python.syntax" else payload["text"]
    if not isinstance(text, str) or len(text.encode("utf-8")) > 1500:
        raise ValueError("Text is invalid or too large (maximum 1500 bytes).")
    if task == "hash.benchmark" and (
        type(payload["iterations"]) is not int or
        not 1000 <= payload["iterations"] <= 50000
    ):
        raise ValueError("Invalid bounded hashing iteration count.")
    wire = canonical(payload)
    if len(wire.encode("utf-8")) > 2048:
        raise ValueError("Portable payload exceeds the broker quota.")
    return wire


def retry_if_locked(operation):
    """SQLite WAL/worker activity can hold a short write lock.

    Retry boundedly; dispatch uses stable request IDs, and all mutations
    are single transactions. Never retry a different payload or a lease.
    """
    @functools.wraps(operation)
    def guarded(*args, **kwargs):
        for attempt in range(5):
            try:
                return operation(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 4:
                    raise
                time.sleep(0.15 * (2 ** attempt))
        raise RuntimeError("Unreachable database retry state")
    return guarded


class EdgePool:
    def __init__(self, broker=None, devices=DEFAULT_DEVICES, *, clock=None):
        self.broker = broker or Broker()
        self.devices = tuple(dict.fromkeys(devices))
        if not self.devices or any(not re.fullmatch(r"[a-z][a-z0-9-]{1,48}", a)
                                   for a in self.devices):
            raise ValueError("Pool device names must be approved app IDs.")
        self.clock = clock or time.time
        with self.broker._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS edge_pool_jobs (
                id TEXT PRIMARY KEY,
                request_key TEXT NOT NULL UNIQUE,
                task TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                device_app TEXT NOT NULL,
                device_job TEXT NOT NULL UNIQUE,
                dispatched REAL NOT NULL,
                moves INTEGER NOT NULL DEFAULT 0,
                observed_status TEXT NOT NULL DEFAULT 'queued',
                created REAL NOT NULL,
                updated REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS edge_pool_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pool_job TEXT NOT NULL REFERENCES edge_pool_jobs(id),
                event TEXT NOT NULL,
                detail TEXT NOT NULL,
                created REAL NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_edge_pool_by_status
                ON edge_pool_jobs(observed_status, updated);
            """)

    def _event(self, db, pool_job, event, detail):
        """Local inspection hash-chain; not an independent device signature."""
        detail = canonical(detail)
        previous = db.execute(
            "SELECT event_hash FROM edge_pool_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prev_hash = previous[0] if previous else "0" * 64
        created = self.clock()
        event_hash = digest({"job": pool_job, "event": event, "detail": detail,
                             "at": created, "previous": prev_hash})
        db.execute(
            "INSERT INTO edge_pool_events(pool_job,event,detail,created,previous_hash,event_hash) "
            "VALUES(?,?,?,?,?,?)",
            (pool_job, event, detail, created, prev_hash, event_hash),
        )

    @retry_if_locked
    def audit(self):
        with self.broker._db() as db:
            rows = db.execute(
                "SELECT pool_job,event,detail,created,previous_hash,event_hash "
                "FROM edge_pool_events ORDER BY id"
            ).fetchall()
        previous = "0" * 64
        for r in rows:
            expected = digest({"job": r["pool_job"], "event": r["event"],
                               "detail": r["detail"], "at": r["created"],
                               "previous": previous})
            if expected != r["event_hash"] or r["previous_hash"] != previous:
                return {"valid": False, "checked_events": len(rows)}
            previous = expected
        return {"valid": True, "checked_events": len(rows),
                "chain_tip": previous,
                "qualification": "Local tamper-evident chaining, not cryptographic device attestation"}

    def _candidates(self, db, task, *, now, exclude=()):
        controls = ResourceGate.controls(db)
        if controls["paused"]:
            return []
        rows = db.execute(
            "SELECT f.app,f.kind,f.allowed_tasks,f.approved,f.charging,"
            "f.free_ram_mb,f.last_seen,a.enabled,a.tasks "
            "FROM fleet_devices f JOIN apps a ON a.app=f.app "
            "WHERE f.app IN (" + ",".join("?" for _ in self.devices) + ")",
            self.devices,
        ).fetchall()
        candidates = []
        for r in rows:
            app = r["app"]
            if app in exclude or r["kind"] not in ("android_arm64", "linux_arm64"):
                continue
            if r["approved"] != 1 or r["enabled"] != 1 or r["charging"] != 1:
                continue
            if (not r["last_seen"] or not 0 <= now-r["last_seen"] <= fleet_devices.MAX_AGE
                    or not isinstance(r["free_ram_mb"], int)
                    or r["free_ram_mb"] < REQUIRED_RAM_MB):
                continue
            if task not in json.loads(r["allowed_tasks"]) or task not in json.loads(r["tasks"]):
                continue
            profile = ResourceGate.profile(db, app, task)
            ram = profile["ram_mb"]
            if (profile["vram_mb"] != 0 or type(ram) is not int
                    or not 64 <= ram <= 256
                    or r["free_ram_mb"] < ram + MIN_RAM_HEADROOM_MB):
                continue
            queued = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE app=? AND status='queued'", (app,)
            ).fetchone()[0]
            active = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE app=? AND status IN ('leased','stranded')",
                (app,),
            ).fetchone()[0]
            if queued >= MAX_PENDING or active >= ResourceGate.app_limit(db,app):
                continue
            recent = db.execute(
                "SELECT status FROM jobs WHERE app=? AND status IN ('completed','failed') "
                "ORDER BY updated DESC LIMIT 12", (app,)
            ).fetchall()
            failures = sum(1 for x in recent if x["status"] == "failed")
            scheduled = db.execute(
                "SELECT COUNT(*) FROM edge_pool_jobs WHERE device_app=? AND created>?",
                (app, now-3600),
            ).fetchone()[0]
            # Prefer free devices, then fairness, recent reliability and more RAM.
            score = (active * 10000 + queued * 1000 + scheduled * 10 +
                     failures * 15 - min(r["free_ram_mb"], 8192) / 512)
            candidates.append({"app": app, "free_ram_mb": r["free_ram_mb"],
                               "queued": queued, "active": active,
                               "recent_failures": failures, "score": score})
        return sorted(candidates, key=lambda x:(x["score"], x["app"]))

    @retry_if_locked
    def devices_status(self):
        now = self.clock()
        with self.broker._db() as db:
            active = {x["app"]: x for x in self._candidates(db,"json.inspect",now=now)}
        all_devices = self.broker.edge_devices()
        return [{"app": d["app"], "kind": d["kind"], "online": d["online"],
                 "approved": d["approved"], "ram_mb": d["free_ram_mb"],
                 "last_seen_age_seconds": (
                     round(max(0, now-d["last_seen"])) if d["last_seen"] is not None
                     else None),
                 "eligible_for_json": d["app"] in active,
                 "queued": active.get(d["app"],{}).get("queued"),
                 "active": active.get(d["app"],{}).get("active")}
                for d in all_devices if d["app"] in self.devices]

    @retry_if_locked
    def submit(self, task, payload, *, request_key, confirm=False):
        if confirm is not True:
            raise PermissionError("Owner confirmation required to dispatch to personal devices.")
        if not isinstance(request_key,str) or not IDEMPOTENCY.fullmatch(request_key):
            raise ValueError("Provide a stable, 8–128-character idempotency key.")
        wire = validate_payload(task, payload)
        signature = digest({"task":task, "payload":payload})
        now = self.clock()
        with self.broker._db() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM edge_pool_jobs WHERE request_key=?", (request_key,)
            ).fetchone()
            if existing is not None:
                if existing["task"] != task or existing["payload_digest"] != signature:
                    raise RuntimeError("Idempotency key reused with different work.")
                db.commit()
                return {"pool_job":existing["id"],"device":existing["device_app"],
                        "job_id":existing["device_job"],"duplicate":True}
            choices = self._candidates(db,task,now=now)
            if not choices:
                raise RuntimeError("No available, fresh and eligible devices; no work dispatched.")
            device = choices[0]["app"]
            pending = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE app=? AND status IN ('queued','leased')",
                (device,),
            ).fetchone()[0]
            total = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            if pending >= MAX_PENDING or total >= MAX_TOTAL:
                raise RuntimeError("Job queue quota reached.")
            pool_id = secrets.token_hex(16)
            job_id = secrets.token_hex(16)
            dev_key = f"pool.{pool_id}.0"
            db.execute(
                "INSERT INTO jobs(id,app,task,payload,status,attempts,created,updated,idempotency_key) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (job_id,device,task,wire,"queued",0,now,now,dev_key),
            )
            db.execute(
                "INSERT INTO edge_pool_jobs(id,request_key,task,payload_digest,"
                "device_app,device_job,dispatched,created,updated) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (pool_id,request_key,task,signature,device,job_id,now,now,now),
            )
            self._event(db,pool_id,"dispatched",
                        {"device":device,"device_job":job_id,"task":task,
                         "choice":choices[0],"payload_sha256":signature})
            db.commit()
        return {"pool_job":pool_id,"device":device,"job_id":job_id,"duplicate":False}

    def _status(self, db, pool_id):
        row = db.execute("SELECT * FROM edge_pool_jobs WHERE id=?", (pool_id,)).fetchone()
        if row is None:
            raise KeyError("Unknown pooled job.")
        job = db.execute(
            "SELECT task,payload,status,attempts,result,lease_until "
            "FROM jobs WHERE id=? AND app=?",
            (row["device_job"],row["device_app"]),
        ).fetchone()
        if job is None:
            raise RuntimeError("Underlying device receipt missing.")
        result = json.loads(job["result"]) if job["result"] else None
        receipt = {
            "pool_job": row["id"], "device": row["device_app"],
            "device_job": row["device_job"], "task": row["task"],
            "status": job["status"], "attempts": job["attempts"],
            "moves": row["moves"], "result": result,
            "result_sha256": digest(result) if result is not None else None,
            "verified": None,
        }
        if job["status"] == "completed":
            try:
                expected = independently_check_task(job["task"],json.loads(job["payload"]))
                receipt["verified"] = result == expected
            except (ValueError,TypeError,OverflowError):
                receipt["verified"] = False
        elif job["status"] in ("failed","stranded"):
            receipt["verified"] = False
        # Integrity and actual correctness are different. A matching receipt
        # that reports invalid JSON/Python is trustworthy but is NOT a pass.
        receipt["check_passed"] = (
            receipt["verified"] is True
            and (job["task"] == "hash.benchmark"
                 or (isinstance(result, dict) and result.get("valid") is True))
        ) if job["status"] == "completed" else None
        return row,job,receipt

    @retry_if_locked
    def status(self,pool_id):
        if not isinstance(pool_id,str) or not JOB_ID.fullmatch(pool_id):
            raise ValueError("Invalid pooled job ID.")
        with self.broker._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row,job,receipt = self._status(db,pool_id)
            if row["observed_status"] != receipt["status"]:
                db.execute(
                    "UPDATE edge_pool_jobs SET observed_status=?,updated=? WHERE id=?",
                    (receipt["status"],self.clock(),pool_id),
                )
                self._event(db,pool_id,"observed",{"device":row["device_app"],
                    "device_job":row["device_job"],"status":receipt["status"],
                    "attempts":job["attempts"],"verified":receipt["verified"],
                    "result_sha256":receipt["result_sha256"]})
            db.commit()
        return receipt

    @retry_if_locked
    def reconcile(self, *, limit=40):
        """Reassign NEVER-CLAIMED pure work after a worker goes offline.

        Atomic across old queued record, new queued record and pooled receipt.
        Never replays leased or previously attempted work on a second phone.
        """
        if not isinstance(limit,int) or not 1 <= limit <= 100:
            raise ValueError("Invalid reconciliation limit.")
        now = self.clock()
        moves = []
        inspections = []
        with self.broker._db() as db:
            for row in db.execute(
                "SELECT id FROM edge_pool_jobs WHERE observed_status IN ('queued','leased','stranded') "
                "ORDER BY created,id LIMIT ?",(limit,)
            ).fetchall():
                pool_id = row["id"]
                db.execute("BEGIN IMMEDIATE")
                record,underlying,receipt = self._status(db,pool_id)
                if record["observed_status"] != receipt["status"]:
                    db.execute("UPDATE edge_pool_jobs SET observed_status=?,updated=? WHERE id=?",
                               (receipt["status"],now,pool_id))
                    self._event(db,pool_id,"observed",{
                        "device":record["device_app"],"status":receipt["status"],
                        "attempts":underlying["attempts"],"verified":receipt["verified"]})
                if (receipt["status"] != "queued" or underlying["attempts"] != 0
                        or underlying["lease_until"] is not None
                        or record["moves"] >= MAX_MOVES
                        or now - record["dispatched"] < 45):
                    inspections.append({"pool_job":pool_id,"status":receipt["status"]})
                    db.commit()
                    continue
                current_choices = self._candidates(db,record["task"],now=now)
                online = any(d["app"]==record["device_app"] for d in current_choices)
                if online:
                    db.commit()
                    continue
                replacements = [d for d in current_choices if d["app"] != record["device_app"]]
                if not replacements:
                    inspections.append({"pool_job":pool_id,"status":"waiting_for_device"})
                    db.commit()
                    continue
                # Critical: both records in ONE IMMEDIATE transaction;
                # if ever leased, rollback and refuse to double execute.
                old_count = db.execute(
                    "UPDATE jobs SET status='failed',updated=?,result=? "
                    "WHERE id=? AND app=? AND status='queued' AND attempts=0 "
                    "AND lease_hash IS NULL AND lease_until IS NULL",
                    (now,canonical({"reason":"reassigned_before_claim"}),
                     record["device_job"],record["device_app"]),
                ).rowcount
                if old_count != 1:
                    db.rollback()
                    continue
                new_id = secrets.token_hex(16)
                new_device = replacements[0]["app"]
                job_payload = underlying["payload"]
                db.execute(
                    "INSERT INTO jobs(id,app,task,payload,status,attempts,created,updated,idempotency_key) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (new_id,new_device,record["task"],job_payload,"queued",0,now,now,
                     f"pool.{pool_id}.{record['moves']+1}"),
                )
                db.execute(
                    "UPDATE edge_pool_jobs SET device_app=?,device_job=?,moves=moves+1,"
                    "dispatched=?,updated=?,observed_status='queued' WHERE id=?",
                    (new_device,new_id,now,now,pool_id),
                )
                self._event(db,pool_id,"reassigned_before_claim",
                            {"from":record["device_app"],"to":new_device,
                             "old_job":record["device_job"],"new_job":new_id})
                db.commit()
                moves.append({"pool_job":pool_id,"from":record["device_app"],"to":new_device})
        return {"reassigned":moves,"inspected":len(inspections),
                "never_replayed_leased_work":True}

    @retry_if_locked
    def report(self, *, limit=20):
        with self.broker._db() as db:
            rows = db.execute(
                "SELECT id FROM edge_pool_jobs ORDER BY created DESC LIMIT ?",
                (min(max(int(limit),1),100),),
            ).fetchall()
            ids = [r["id"] for r in rows]
        jobs = [self.status(job) for job in ids]
        totals = {status:sum(j["status"]==status for j in jobs)
                  for status in ("queued","leased","completed","failed","stranded")}
        totals.update({
            "checks_passed":sum(j["check_passed"] is True for j in jobs),
            "checks_failed":sum(j["status"]=="completed" and
                                j["check_passed"] is False for j in jobs),
            "verified_receipts":sum(j["verified"] is True for j in jobs),
            "integrity_failures":sum(j["status"]=="completed" and
                                     j["verified"] is False for j in jobs),
        })
        return {"devices":self.devices_status(),"jobs":jobs,"totals":totals,
                "audit":self.audit(),
                "scope":"Existing reviewed Python syntax, JSON inspection, bounded PBKDF2 only"}
