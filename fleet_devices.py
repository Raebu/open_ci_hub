"""Private approved edge-device registry: scoped identity, TTL and real capacity.

Works with existing Tailscale/TLS + app worker tokens. Only approved, owner-owned
devices; free cloud VMs are never presumed free or automatically enrolled.
"""
from __future__ import annotations
import json
import re
import time

SUPPORTED=frozenset(("python.syntax","json.inspect","hash.benchmark"))
KINDS={"windows_arm64":("Windows","arm64"),"android_arm64":("Android","arm64"),
       "linux_arm64":("Linux","arm64"),"linux_x86_64":("Linux","x86_64")}
SHA=re.compile(r"^[0-9a-f]{64}$")
MAX_AGE=120

def setup(db):
    db.execute("""
      CREATE TABLE IF NOT EXISTS fleet_devices(
        app TEXT PRIMARY KEY REFERENCES apps(app),
        credential_hash TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL,
        allowed_tasks TEXT NOT NULL,
        approved INTEGER NOT NULL,
        free_ram_mb INTEGER,
        charging INTEGER,
        last_seen REAL NOT NULL DEFAULT 0,
        created REAL NOT NULL
      )
    """)

def enroll(db,app,kind,*,confirm=False):
    if confirm is not True:
        raise PermissionError("Owner approval required for device enrolment.")
    if kind not in KINDS or not isinstance(app,str):
        raise ValueError("Only owner-owned Android, Windows and Linux devices are supported.")
    row=db.execute("SELECT tasks FROM apps WHERE app=? AND enabled=1",(app,)).fetchone()
    if not row:
        raise ValueError("App must be registered before device enrolment.")
    tasks=json.loads(row["tasks"])
    if not tasks or not set(tasks)<=SUPPORTED:
        raise ValueError("Only bounded and reviewed portable edge tasks may run remotely.")
    creds=db.execute("SELECT token_hash FROM credentials WHERE app=? AND role='worker' AND revoked=0",
                      (app,)).fetchall()
    if len(creds)!=1 or not SHA.fullmatch(creds[0]["token_hash"]):
        raise ValueError("Each device app requires exactly one active worker credential.")
    db.execute("INSERT INTO fleet_devices(app,credential_hash,kind,allowed_tasks,approved,created) "
               "VALUES(?,?,?,?,?,?)",(app,creds[0]["token_hash"],kind,json.dumps(tasks),1,time.time()))
    return {"app":app,"kind":kind,"tasks":tasks,"approved":True,
            "billing_policy":"owned_device_only_no_cloud_enrolment"}

def report(db,who,data,*,now=None):
    if who.get("role")!="worker":
        raise PermissionError("Only worker credentials may report device health.")
    row=db.execute("SELECT * FROM fleet_devices WHERE app=?",(who.get("app"),)).fetchone()
    if not row:
        return {"enrolled":False,"online":False,"reason":"owner_enrolment_required"}
    if (row["approved"]!=1 or who.get("credential_hash")!=row["credential_hash"]):
        raise PermissionError("Worker token is not bound to this approved device.")
    if not isinstance(data,dict) or set(data)!={"platform","architecture","free_ram_mb","charging"}:
        raise ValueError("Unexpected device-health format.")
    kind=KINDS[row["kind"]]
    if data["platform"]!=kind[0] or data["architecture"]!=kind[1]:
        raise ValueError("Device architecture/platform did not match the owner-approved record.")
    remaining=data["free_ram_mb"]
    if type(remaining) is not int or not 0<=remaining<=262144 or type(data["charging"]) is not bool:
        raise ValueError("Invalid resource telemetry.")
    now=time.time() if now is None else now
    db.execute("UPDATE fleet_devices SET free_ram_mb=?,charging=?,last_seen=? WHERE app=?",
               (remaining,int(data["charging"]),now,who["app"]))
    ready=data["charging"] and remaining>=1536
    return {"enrolled":True,"online":ready,"reason":"ready" if ready else "device_busy_or_not_powered"}

def admit(db,who,task,ram_mb,*,now=None):
    row=db.execute("SELECT * FROM fleet_devices WHERE app=?",(who.get("app"),)).fetchone()
    if not row:
        return None # legacy normal resource-gated worker
    if (row["approved"]!=1 or row["credential_hash"]!=who.get("credential_hash")):
        return "unapproved_device"
    now=time.time() if now is None else now
    if row["last_seen"]<=0 or now-row["last_seen"]>MAX_AGE or now<row["last_seen"]:
        return "device_telemetry_stale"
    if task not in json.loads(row["allowed_tasks"]):
        return "device_task_not_approved"
    if row["charging"]!=1 or row["free_ram_mb"] is None or row["free_ram_mb"]<ram_mb+1024:
        return "remote_device_foreground_priority"
    if type(ram_mb) is not int or not 64<=ram_mb<=256:
        return "remote_device_ram_budget_over_limit"
    return "ready"

def listing(db,*,now=None):
    now=time.time() if now is None else now
    rows=db.execute("SELECT app,kind,allowed_tasks,approved,free_ram_mb,charging,last_seen "
                    "FROM fleet_devices ORDER BY app").fetchall()
    results=[]
    for row in rows:
        fresh=(row["approved"]==1 and row["last_seen"]>0 and
               0<=now-row["last_seen"]<=MAX_AGE)
        results.append({"app":row["app"],"kind":row["kind"],"architecture":KINDS[row["kind"]][1],
                        "tasks":json.loads(row["allowed_tasks"]),"approved":bool(row["approved"]),
                        "online":bool(fresh and row["charging"] and row["free_ram_mb"] is not None
                                      and row["free_ram_mb"]>=1536),
                        "free_ram_mb":row["free_ram_mb"] if fresh else None,
                        "billing":"owned_device_only","last_seen":row["last_seen"] or None})
    return results
