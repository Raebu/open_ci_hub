#!/usr/bin/env python3
"""RaeburnAI Mission Control: bounded, evidence-led, opt-in task orchestration.

Only fixed low-risk reads, local/private AI and approved memory sync execute.
No browser control, external account writes, payments, deployments or arbitrary
shell. Production obligations remain with their canonical systems.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".local" / "raeburn-command"
DB = STATE / "missions.sqlite3"
SAFE = frozenset({"operations.snapshot", "ai.memory", "models.status", "ai.brief"})
TENANTS = frozenset({"raeburn-group"})
MODES = frozenset({"observe", "assist", "autonomous"})
ID = re.compile(r"^[a-f0-9]{32}$")
IDEMPOTENCY = re.compile(r"^[a-zA-Z0-9_-]{8,80}$")
POSSIBLE_PERSONAL = re.compile(r"(?i)(?:\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b|\b(?:password|api[_ -]?key|bearer|secret|access[_ -]?token)\s*[=:]\s*\S+|(?:\+?\d[\d ()-]{9,}\d))")
EXTERNAL = re.compile(r"\b(send|email|deploy|release|merge|pay|purchase|book|apply|submit|publish|follow|message|contact|reach out|finish|complete|fix|build|implement|write to|delete|remove|change|update|edit)\b", re.I)

def now(): return time.time()

def clean_objective(objective):
    if not isinstance(objective,str):
        raise ValueError("Objective must be text")
    objective=" ".join(objective.split())
    if not 8<=len(objective)<=280 or any(ord(c)<32 for c in objective):
        raise ValueError("Objective must be 8–280 characters")
    if POSSIBLE_PERSONAL.search(objective):
        raise ValueError("Remove private contact, financial and secret data from mission title")
    return objective

def choose_lane(objective):
    s=objective.lower()
    mapping=(("funding",("grant","funding","bid","tender")),
             ("sales",("lead","sales","client","recruitment","outreach")),
             ("product",("code","repository","test","deploy","product","ats","crm","office","website","software")),
             ("portfolio",("venture","partnership","gibp","portfolio")),
             ("coo",("calendar","meeting","schedule","obligation","director","today","important","priority")))
    for lane,terms in mapping:
        if any(t in s for t in terms):return lane
    return "coo"

def plan(objective,mode="assist"):
    objective=clean_objective(objective)
    if mode not in MODES:raise ValueError("Invalid autonomy mode")
    lane=choose_lane(objective)
    action=bool(EXTERNAL.search(objective))
    steps=["operations.snapshot","ai.memory","models.status"]
    if mode!="observe":steps.append("ai.brief")
    if action:steps.append("owner.handoff")
    return {"objective":objective,"lane":lane,"mode":mode,"steps":steps,
            "external_action_requested":action,
            "scope":"read-only-metadata-and-private-local-AI",
            "approval_needed":action,
            "can_execute_external_action":False}

def _store():
    if STATE.is_symlink() or DB.is_symlink():
        raise PermissionError("Symlinked Command Centre data refused")
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    st=STATE.stat()
    if st.st_uid!=os.getuid():raise PermissionError("Wrong owner for Command Centre state")
    STATE.chmod(0o700)
    if DB.exists():
        st=DB.stat()
        if not DB.is_file() or st.st_uid!=os.getuid() or (st.st_mode&0o777)!=0o600:
            raise PermissionError("Command Centre database requires 0600 owner-only")

@contextmanager
def db():
    _store()
    conn=sqlite3.connect(DB,timeout=12,isolation_level=None)
    conn.row_factory=sqlite3.Row
    try:
        os.chmod(DB,0o600)
        conn.execute("PRAGMA busy_timeout=12000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""CREATE TABLE IF NOT EXISTS missions(
            id TEXT PRIMARY KEY,tenant TEXT NOT NULL,objective TEXT NOT NULL,
            lane TEXT NOT NULL,mode TEXT NOT NULL,status TEXT NOT NULL,
            actor TEXT NOT NULL,external_requested INTEGER NOT NULL,
            idempotency_key TEXT UNIQUE,created REAL NOT NULL,updated REAL NOT NULL,
            last_error TEXT);
          CREATE TABLE IF NOT EXISTS steps(
            mission TEXT NOT NULL,ord INTEGER NOT NULL,kind TEXT NOT NULL,
            status TEXT NOT NULL,started REAL,updated REAL NOT NULL,
            receipt TEXT,summary TEXT,attempts INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(mission,ord),
            FOREIGN KEY(mission) REFERENCES missions(id));
          CREATE TABLE IF NOT EXISTS audit(
            seq INTEGER PRIMARY KEY AUTOINCREMENT,mission TEXT NOT NULL,
            event TEXT NOT NULL,detail TEXT NOT NULL,at REAL NOT NULL,
            previous_hash TEXT NOT NULL,event_hash TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS ix_mission_status ON missions(status,updated);
          CREATE INDEX IF NOT EXISTS ix_steps_status ON steps(status,mission);""")
        yield conn
    finally:
        conn.close()

def _event(conn,mission,event,detail,at):
    # SQLite REAL normalises integer timestamps to float on readback.
    # Hash the exact persisted type, including during mocked clock tests.
    at=float(at)
    # Avoid leaking prompt, accounts or evidence content to audit trail.
    previous=conn.execute("SELECT event_hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
    last=previous["event_hash"] if previous else "0"*64
    contents=json.dumps([mission,event,detail,at,last],ensure_ascii=True,separators=(",",":"))
    digest=hashlib.sha256(contents.encode()).hexdigest()
    conn.execute("INSERT INTO audit(mission,event,detail,at,previous_hash,event_hash) VALUES(?,?,?,?,?,?)",
                 (mission,event,detail,at,last,digest))

def _audit_ok(conn):
    records=conn.execute("SELECT mission,event,detail,at,previous_hash,event_hash FROM audit ORDER BY seq").fetchall()
    last="0"*64
    for row in records:
        body=json.dumps([row["mission"],row["event"],row["detail"],row["at"],last],
                        ensure_ascii=True,separators=(",",":"))
        if row["previous_hash"]!=last or hashlib.sha256(body.encode()).hexdigest()!=row["event_hash"]:
            return False
        last=row["event_hash"]
    return True

def audit_valid():
    with db() as conn:
        return _audit_ok(conn)

def _receipts_ok(conn):
    """Fail closed on corrupted or truncated completed-step evidence."""
    rows=conn.execute("SELECT summary,receipt FROM steps WHERE status='completed'").fetchall()
    for item in rows:
        try:
            if _receipt(json.loads(item["summary"])) != item["receipt"]:
                return False
        except (ValueError,TypeError,KeyError):
            return False
    return True

def submit(objective,*,mode="assist",actor="local-owner",tenant="raeburn-group",key=None):
    spec=plan(objective,mode)
    if tenant not in TENANTS: raise PermissionError("Unregistered tenant")
    if not isinstance(actor,str) or not 1<=len(actor)<=120 or any(ord(c)<32 for c in actor):
        raise ValueError("Invalid actor")
    if key is not None and (not isinstance(key,str) or not IDEMPOTENCY.fullmatch(key)):
        raise ValueError("Invalid idempotency key")
    at=now()
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _audit_ok(conn) or not _receipts_ok(conn):
            raise PermissionError("Mission evidence integrity failed; no new work permitted")
        if key:
            row=conn.execute("SELECT id,objective,mode,tenant,actor FROM missions WHERE idempotency_key=?",(key,)).fetchone()
            if row:
                if (row["objective"]!=spec["objective"] or row["mode"]!=mode
                        or row["tenant"]!=tenant or row["actor"]!=actor):
                    raise ValueError("Idempotency key belongs to another mission or identity")
                return {"id":row["id"],"duplicate":True,"plan":spec}
        pending=conn.execute("SELECT count(*) FROM missions WHERE status IN ('queued','running','paused','awaiting_approval','needs_reconciliation')").fetchone()[0]
        if pending>=60:raise RuntimeError("Mission queue full; resolve existing work first")
        mission=uuid.uuid4().hex
        conn.execute("INSERT INTO missions(id,tenant,objective,lane,mode,status,actor,external_requested,idempotency_key,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (mission,tenant,spec["objective"],spec["lane"],mode,"queued",actor,int(spec["external_action_requested"]),key,at,at))
        for i,kind in enumerate(spec["steps"]):
            conn.execute("INSERT INTO steps(mission,ord,kind,status,updated) VALUES(?,?,?,?,?)",
                         (mission,i,kind,"pending",at))
        _event(conn,mission,"created","mode="+mode+";lane="+spec["lane"],at)
        conn.commit()
    return {"id":mission,"duplicate":False,"plan":spec}

def list_missions(limit=20):
    if type(limit) is not int or not 1<=limit<=50:raise ValueError("Invalid limit")
    with db() as conn:
        rows=conn.execute("SELECT * FROM missions ORDER BY updated DESC LIMIT ?",(limit,)).fetchall()
        counters=conn.execute("""SELECT mission,count(*) total,
          sum(CASE WHEN status='completed' THEN 1 ELSE 0 END) finished,
          sum(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed
          FROM steps GROUP BY mission""").fetchall()
        byid={r["mission"]:dict(r) for r in counters}
        return [{"id":r["id"],"objective":r["objective"],"lane":r["lane"],"mode":r["mode"],
                 "status":r["status"],"created":r["created"],"updated":r["updated"],
                 "last_error":r["last_error"],"steps_total":byid.get(r["id"],{}).get("total",0),
                 "steps_completed":byid.get(r["id"],{}).get("finished",0),
                 "steps_failed":byid.get(r["id"],{}).get("failed",0),
                 "external_action_performed":False}
                 for r in rows]

def inspect(mission):
    if not isinstance(mission,str) or not ID.fullmatch(mission):
        raise ValueError("Invalid mission identifier")
    with db() as conn:
        mission_row=conn.execute("SELECT * FROM missions WHERE id=?",(mission,)).fetchone()
        if not mission_row:raise ValueError("Mission not found")
        steps=conn.execute("SELECT ord,kind,status,receipt,summary,updated FROM steps WHERE mission=? ORDER BY ord",
                           (mission,)).fetchall()
    results=[]
    for row in steps:
        step=dict(row)
        step["receipt_valid"]=False
        if step["status"]=="completed" and step["receipt"] and step["summary"]:
            try:
                decoded=json.loads(step["summary"])
                step["receipt_valid"]=(_receipt(decoded)==step["receipt"])
            except (TypeError,ValueError):
                pass
        results.append(step)
    return {"mission":dict(mission_row),"steps":results}

def control(mission,action,*,confirm=False,actor="local-owner"):
    if not confirm:raise PermissionError("Explicit approval required")
    if action not in ("pause","resume","cancel","approve_handoff","reconcile","retry_safe"):
        raise ValueError("Unknown control")
    if not isinstance(mission,str) or not ID.fullmatch(mission):
        raise ValueError("Invalid mission")
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _audit_ok(conn) or not _receipts_ok(conn):
            raise PermissionError("Mission evidence integrity failed; controls suspended")
        row=conn.execute("SELECT status FROM missions WHERE id=?",(mission,)).fetchone()
        if not row:raise ValueError("Mission not found")
        status=row["status"]
        running=conn.execute("SELECT count(*) FROM steps WHERE mission=? AND status='running'",(mission,)).fetchone()[0]
        if action=="pause" and status in ("queued","running") and not running:target="paused"
        elif action=="resume" and status=="paused" and not running:target="queued"
        elif action=="cancel" and status in ("queued","paused","awaiting_approval","needs_reconciliation") and not running:target="cancelled"
        elif action=="approve_handoff" and status=="awaiting_approval" and not running:
            # Approval does NOT purport to execute actions which lack an approved backend.
            conn.execute("UPDATE steps SET status='handoff_approved',summary='Owner approved handoff; external execution remains pending.',updated=? WHERE mission=? AND kind='owner.handoff'",
                         (now(),mission))
            target="handoff_ready"
        elif action=="reconcile" and status=="needs_reconciliation" and not running:
            # No automatic replay; owner must investigate externally before any fresh mission.
            target="needs_review"
        elif action=="retry_safe" and status=="failed" and not running:
            row=conn.execute("SELECT ord,kind,attempts FROM steps WHERE mission=? AND status='failed' LIMIT 1",(mission,)).fetchone()
            if row is None or row["kind"] not in SAFE or row["attempts"]>=2:
                raise RuntimeError("Retry not authorised or limit reached")
            conn.execute("UPDATE steps SET status='pending',receipt=NULL,summary=NULL,started=NULL,updated=? WHERE mission=? AND ord=?",
                         (now(),mission,row["ord"]))
            target="queued"
        else:raise RuntimeError("Action not allowed in current mission state")
        at=now()
        conn.execute("UPDATE missions SET status=?,updated=? WHERE id=?",(target,at,mission))
        _event(conn,mission,"owner."+action,"state="+target,at)
        conn.commit()
    return {"id":mission,"status":target,"external_action_performed":False}

def _snapshot():
    source=ROOT/".local"/"business-execution"/"control.sqlite"
    if not source.exists() or source.is_symlink():return {"available":False,"reason":"ledger_unavailable"}
    conn=sqlite3.connect("file:"+str(source)+"?mode=ro",uri=True,timeout=4)
    try:
        rows=conn.execute("SELECT lane,COUNT(*) FROM work GROUP BY lane").fetchall()
        count=conn.execute("SELECT COUNT(*) FROM event").fetchone()[0]
        return {"available":True,"events":count,"lanes":dict(rows)}
    finally:conn.close()

def attention_signals(limit=6):
    """Read-only, evidence-backed operational risks; counts, not customer records."""
    if not isinstance(limit,int) or not 1<=limit<=12:raise ValueError("Invalid signal limit")
    source=ROOT/".local"/"business-execution"/"control.sqlite"
    if not source.is_file() or source.is_symlink():return []
    con=sqlite3.connect("file:"+str(source)+"?mode=ro",uri=True,timeout=3)
    try:
        rows=con.execute("""SELECT lane,stage,COUNT(*) AS n FROM work
                            GROUP BY lane,stage ORDER BY n DESC LIMIT 100""").fetchall()
    finally:con.close()
    signals=[]
    keywords=("needs_evidence","unverified","requires_external_readback",
              "needs_review","not_cutover_ready","requires_trusted_checks",
              "requires_provenance","protected_read_only")
    for lane,stage,count in rows:
        if lane not in ("sales","funding","product","portfolio","coo") or not isinstance(stage,str):
            continue
        if not any(term in stage for term in keywords):continue
        if not re.fullmatch(r"[a-z0-9_]{1,80}",stage):continue
        signals.append({"lane":lane,"reason":stage.replace("_"," "),
                        "count":int(count),"action":"review_evidence",
                        "verified_outcome":False})
    return signals[:limit]

def _execute(kind,lane,objective):
    if kind=="operations.snapshot":return _snapshot()
    if kind=="ai.memory":
        from raeburn_ai import sync_business_transactions, status
        outcome=sync_business_transactions()
        st=status()
        return {"sync":outcome.get("status"),"new_events":outcome.get("ingested",0),
                "memory_total":st["memory_events"]}
    if kind=="models.status":
        from free_ai_router import check_status
        return {"providers":[{"name":x["provider"],"eligible":x["eligible"]}
                             for x in check_status()],"private_processing":"local_only"}
    if kind=="ai.brief":
        from raeburn_ai import ask
        # No raw customer data or web resources. Uses private Samsung model only.
        question=("Review "+lane+" operations for the objective: "+objective+
                  ". Provide a brief evidence-based summary, missing evidence and next steps. "
                  "Do not assert that any external action has been performed.")
        try:
            result=ask("raeburn-group",question,include_observations=True,max_tokens=96)
        except (OSError,RuntimeError,TimeoutError,ValueError):
            result=None
        if result and result.get("ok"):
            return {"answer":str(result["answer"])[:900],"model":result["model"],
                    "memory_items":result["memory_items"],"cloud":False,
                    "degraded":False,"evidence_status":"unverified_operational_observations"}
        # No cloud or fabricated AI response. Return genuinely observed,
        # bounded stage-count metadata as a clear degraded-mode briefing.
        signals=[x for x in attention_signals(4) if x["lane"]==lane]
        if not signals:
            signals=attention_signals(3)
        report=("Local AI inference unavailable. Evidence-only report; "
                "these stage counts do not prove completion. "+
                "; ".join(f'{x["lane"]}: {x["count"]} {x["reason"]}' for x in signals))
        return {"answer":report[:900],"model":"evidence-only-local-fallback",
                "memory_items":0,"cloud":False,"degraded":True,
                "evidence_status":"unverified_operational_observations"}
    raise PermissionError("Not an approved task")

def _receipt(value):
    wire=json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False)
    return hashlib.sha256(wire.encode()).hexdigest()

def run_once(*,executor=_execute,clock=now):
    """One atomic claim; no automatic replay after crash or interrupted execution."""
    at=clock()
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _audit_ok(conn) or not _receipts_ok(conn):
            raise PermissionError("Mission evidence integrity failed; execution suspended")
        # Any prior running step older than its timeout is NOT retried.
        interrupted=conn.execute("""SELECT s.mission,s.ord FROM steps s JOIN missions m ON m.id=s.mission
             WHERE s.status='running' AND s.started<?""",(at-150,)).fetchall()
        for r in interrupted:
            conn.execute("UPDATE steps SET status='interrupted',updated=? WHERE mission=? AND ord=? AND status='running'",
                         (at,r["mission"],r["ord"]))
            conn.execute("UPDATE missions SET status='needs_reconciliation',updated=?,last_error='interrupted_step' WHERE id=?",
                         (at,r["mission"]))
            _event(conn,r["mission"],"interrupted","manual_reconciliation_required",at)
        # Only one active mission step at a time across the whole worker.
        active=conn.execute("SELECT count(*) FROM steps WHERE status='running'").fetchone()[0]
        if active:
            conn.commit()
            return {"state":"busy","reason":"previous_step_still_running"}
        row=conn.execute("""SELECT m.id,m.lane,m.objective,s.ord,s.kind
            FROM missions m JOIN steps s ON s.mission=m.id
            WHERE m.status IN ('queued','running') AND s.status='pending'
            AND NOT EXISTS (SELECT 1 FROM steps earlier WHERE earlier.mission=m.id
                    AND earlier.ord<s.ord AND earlier.status!='completed')
            ORDER BY m.created,s.ord LIMIT 1""").fetchone()
        if row is None:
            conn.commit();return {"state":"idle"}
        mission=row["id"];index=row["ord"];kind=row["kind"]
        if kind=="owner.handoff":
            conn.execute("UPDATE steps SET status='awaiting_approval',updated=? WHERE mission=? AND ord=?",
                         (at,mission,index))
            conn.execute("UPDATE missions SET status='awaiting_approval',updated=? WHERE id=?",(at,mission))
            _event(conn,mission,"approval_required","external_action_not_executed",at)
            conn.commit()
            return {"state":"awaiting_approval","id":mission}
        if kind not in SAFE:raise PermissionError("Unapproved task type in queue")
        conn.execute("UPDATE steps SET status='running',started=?,updated=?,attempts=attempts+1 WHERE mission=? AND ord=?",
                     (at,at,mission,index))
        conn.execute("UPDATE missions SET status='running',updated=? WHERE id=?",(at,mission))
        _event(conn,mission,"step_started","kind="+kind,at)
        conn.commit()
    try:
        value=executor(kind,row["lane"],row["objective"])
        if not isinstance(value,dict) or value.get("ok") is False:
            raise ValueError("Executor failed or returned invalid result")
        # Store a complete canonical result so each SHA-256 receipt can be
        # recalculated from the persisted evidence, including after restart.
        summary=json.dumps(value,sort_keys=True,separators=(",",":"),
                           ensure_ascii=True,allow_nan=False)
        if len(summary.encode("utf-8"))>4096:
            raise ValueError("result_exceeds_evidence_limit")
        digest=hashlib.sha256(summary.encode("utf-8")).hexdigest()
        success=True
    except Exception:
        digest=None;summary="Task execution unavailable; inspect owner-local service evidence."
        success=False
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _audit_ok(conn) or not _receipts_ok(conn):
            raise PermissionError("Mission evidence integrity changed during execution; result quarantined")
        state=conn.execute("SELECT status FROM missions WHERE id=?",(mission,)).fetchone()
        if state is None or state["status"]=="needs_reconciliation":
            conn.commit();return {"state":"needs_reconciliation","id":mission}
        finished=clock()
        conn.execute("UPDATE steps SET status=?,receipt=?,summary=?,updated=? WHERE mission=? AND ord=?",
                     ("completed" if success else "failed",digest,summary,finished,mission,index))
        if success:
            rest=conn.execute("SELECT COUNT(*) FROM steps WHERE mission=? AND status='pending'",(mission,)).fetchone()[0]
            status="queued" if rest else "completed"
        else:status="failed"
        conn.execute("UPDATE missions SET status=?,updated=?,last_error=? WHERE id=?",
                     (status,finished,None if success else "step_failed",mission))
        _event(conn,mission,"step_ok" if success else "step_failed","kind="+kind,finished)
        conn.commit()
    return {"state":"completed_step" if success else "failed_step","id":mission,
            "task":kind,"receipt":digest,"mission_status":status}

def create_daily_brief(day=None):
    if day is None:day=time.strftime("%Y%m%d",time.localtime())
    if not re.fullmatch(r"\d{8}",day):raise ValueError("Invalid date")
    return submit("Review today's priorities and verified business operations",
                  mode="assist",actor="scheduled-local",tenant="raeburn-group",
                  key="daily-"+day)

def overview():
    missions=list_missions()
    status={}
    for item in missions: status[item["status"]]=status.get(item["status"],0)+1
    from raeburn_ai import status as ai_status
    from raeburn_command_health import health
    return {"name":"RaeburnAI Command Centre","missions":missions,
            "readiness":health(),
            "counts":status,"attention":attention_signals(),"memory":ai_status(),
            "audit_ok":audit_valid(),
            "execution":"fixed-approved-local-actions-only",
            "external_actions":"approval_and_separate_implementation_required"}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="cmd",required=True)
    sub.add_parser("status")
    sub.add_parser("work")
    sub.add_parser("daily-brief")
    draft=sub.add_parser("preview")
    draft.add_argument("objective")
    draft.add_argument("--mode",choices=sorted(MODES),default="assist")
    create=sub.add_parser("create")
    create.add_argument("objective")
    create.add_argument("--mode",choices=sorted(MODES),default="assist")
    ident=sub.add_parser("inspect");ident.add_argument("mission")
    action=sub.add_parser("control");action.add_argument("mission");action.add_argument("action",choices=("pause","resume","cancel","approve_handoff","reconcile","retry_safe"));action.add_argument("--confirm",action="store_true")
    args=p.parse_args(argv)
    try:
        if args.cmd=="status":result=overview()
        elif args.cmd=="work":result=run_once()
        elif args.cmd=="daily-brief":result=create_daily_brief()
        elif args.cmd=="preview":result=plan(args.objective,args.mode)
        elif args.cmd=="create":result=submit(args.objective,mode=args.mode)
        elif args.cmd=="inspect":result=inspect(args.mission)
        else:result=control(args.mission,args.action,confirm=args.confirm)
        print(json.dumps(result,ensure_ascii=False))
        return 0
    except (OSError,ValueError,PermissionError,RuntimeError,sqlite3.Error) as exc:
        print(json.dumps({"error":str(exc)[:160]}),file=sys.stderr)
        return 2
if __name__=="__main__":raise SystemExit(main())
