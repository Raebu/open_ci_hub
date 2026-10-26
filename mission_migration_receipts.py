"""Crash-consistent transfer receipts for moving idempotent tiny jobs.

A crashed lease NEVER automatically replays a potentially mutating task.
This is independent of the connected network transport.
"""
from __future__ import annotations
import hashlib
import json
import sqlite3
import time
from autonomy_core import canonical

TYPES=frozenset(("digest","inspect","json.inspect","python.syntax","hash.benchmark"))

def setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS migration_receipts(
      job_id TEXT PRIMARY KEY, digest TEXT NOT NULL, owner TEXT NOT NULL,
      recipient TEXT NOT NULL, state TEXT NOT NULL, acquired REAL NOT NULL,
      completed REAL, result_sha256 TEXT)""")

def claim(db,job_id,body,source,target,*,now=None):
    if (not isinstance(job_id,str) or not job_id.isidentifier()
        or body.get("kind") not in TYPES or not isinstance(source,str)
        or not isinstance(target,str) or source==target):
        raise ValueError("Unsupported migration candidate")
    encoded=canonical(body).encode()
    if len(encoded)>2048:
        raise ValueError("Transfer too large")
    digest=hashlib.sha256(encoded).hexdigest()
    now=time.time() if now is None else now
    setup(db)
    db.execute("BEGIN IMMEDIATE")
    existing=db.execute("SELECT digest,state FROM migration_receipts WHERE job_id=?",
                        (job_id,)).fetchone()
    if existing:
        db.rollback()
        if existing[0]!=digest:
            raise ValueError("Conflicting duplicate migration")
        return {"claim":"already_claimed","state":existing[1]}
    db.execute("INSERT INTO migration_receipts(job_id,digest,owner,recipient,state,acquired)"
               "VALUES(?,?,?,?,?,?)",(job_id,digest,source,target,"reserved",now))
    db.commit()
    return {"claim":"reserved","digest":digest}

def complete(db,job_id,receipt,*,now=None):
    encoded=canonical(receipt).encode()
    if len(encoded)>8192:
        raise ValueError("Oversized evidence")
    digest=hashlib.sha256(encoded).hexdigest()
    now=time.time() if now is None else now
    db.execute("BEGIN IMMEDIATE")
    row=db.execute("SELECT state,result_sha256 FROM migration_receipts WHERE job_id=?",
                   (job_id,)).fetchone()
    if not row or row[0]!="reserved":
        db.rollback()
        raise ValueError("Missing or already-finalised lease")
    db.execute("UPDATE migration_receipts SET state='completed',completed=?,"
               "result_sha256=? WHERE job_id=?",(now,digest,job_id))
    db.commit()
    return {"state":"completed","evidence_sha256":digest}

def reconcile(db,*,now=None,max_age=120):
    now=time.time() if now is None else now
    if max_age<10:
        raise ValueError("Lease duration too short")
    rows=db.execute("SELECT job_id,recipient FROM migration_receipts "
                    "WHERE state='reserved' AND acquired<?",
                    (now-max_age,)).fetchall()
    return [{"job_id":r[0],"device":r[1],
             "status":"needs_reconciliation_no_automatic_replay"} for r in rows]
