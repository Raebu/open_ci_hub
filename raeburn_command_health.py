#!/usr/bin/env python3
"""Evidence-backed local RaeburnAI release readiness, no secrets or private payloads."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import time
from contextlib import closing

import raeburn_command_centre as cc
import raeburn_command_backup as backup

SERVICE_UNITS=("open-ci-hub-dashboard.service",
               "raeburn-command-worker.timer",
               "raeburn-command-daily.timer",
               "raeburn-command-backup.timer",
               "raeburn-ai-sync.timer")

def _service_status():
    try:
        p=subprocess.run(("systemctl","--user","is-active",*SERVICE_UNITS),
                         capture_output=True,text=True,timeout=4,check=False)
        lines=p.stdout.splitlines()
        return {name:(lines[i]=="active" if i<len(lines) else False)
                for i,name in enumerate(SERVICE_UNITS)}
    except (OSError,subprocess.TimeoutExpired):
        return {name:False for name in SERVICE_UNITS}

def _receipt_integrity():
    """Recompute every completed step receipt from the complete persisted JSON."""
    with cc.db() as conn:
        steps=conn.execute("SELECT summary,receipt FROM steps WHERE status='completed'").fetchall()
        stalled=conn.execute("SELECT count(*) FROM missions WHERE status='needs_reconciliation'").fetchone()[0]
        failed=conn.execute("SELECT count(*) FROM missions WHERE status='failed'").fetchone()[0]
    invalid=0
    for item in steps:
        try:
            data=json.loads(item["summary"])
            valid=(cc._receipt(data)==item["receipt"])
        except (ValueError,TypeError,KeyError):
            valid=False
        if not valid:invalid+=1
    return {"checked":len(steps),"invalid":invalid,"needs_reconciliation":stalled,"failed_missions":failed}

def _backup_status():
    result={"present":False,"integrity_verified":False,"separate_filesystem":False,
            "fresh":False,"age_hours":None,"offsite_verified":False}
    try:
        source=cc.DB
        destination=backup.DEST
        if not source.is_file() or destination.is_symlink() or not destination.is_dir():
            return result
        result["separate_filesystem"]=source.stat().st_dev!=destination.stat().st_dev
        candidates=sorted((f for f in destination.iterdir() if backup.FILE.fullmatch(f.name)
                          and f.is_file() and not f.is_symlink()),reverse=True)
        if not candidates:return result
        newest=candidates[0]
        result["present"]=True
        age=max(0,time.time()-newest.stat().st_mtime)
        result["age_hours"]=round(age/3600,2)
        if age>30*3600:return result
        manifest=destination/(newest.name+".json")
        if not manifest.is_file() or manifest.is_symlink():return result
        data=json.loads(manifest.read_text(encoding="utf8"))
        if data.get("name")!=newest.name or data.get("sha256")!=hashlib.sha256(newest.read_bytes()).hexdigest():
            return result
        check=backup.verify(newest)
        result["integrity_verified"]=(check.get("sqlite_integrity")=="ok" and
                                      check.get("audit_chain") is True)
        result["fresh"]=result["integrity_verified"]
    except (OSError,ValueError,PermissionError,sqlite3.Error,RuntimeError):
        pass
    return result

def health(*,units=None,backup_data=None,receipt_data=None):
    """No network calls or customer data; may be exposed to approved management."""
    if units is None:units=_service_status()
    if backup_data is None:backup_data=_backup_status()
    if receipt_data is None:receipt_data=_receipt_integrity()
    try:
        audit_ok=cc.audit_valid()
    except (OSError,sqlite3.Error,PermissionError):
        audit_ok=False
    issues=[]
    if not audit_ok:issues.append("audit_integrity_failed")
    if receipt_data["invalid"]:issues.append("step_receipts_unverifiable")
    if receipt_data["needs_reconciliation"]:issues.append("interrupted_missions_need_review")
    if receipt_data["failed_missions"]:issues.append("failed_missions_need_review")
    if not all(units.values()):issues.append("required_local_services_missing")
    if not backup_data["fresh"]:issues.append("recent_verified_backup_missing")
    if not backup_data["separate_filesystem"]:issues.append("backup_not_on_distinct_volume")
    if not backup_data["offsite_verified"]:issues.append("encrypted_offsite_recovery_unverified")
    blocking={"audit_integrity_failed","step_receipts_unverifiable",
              "required_local_services_missing","recent_verified_backup_missing",
              "backup_not_on_distinct_volume"}
    return {"local_release_ready":not any(i in blocking for i in issues),
            "disaster_recovery_complete":False,
            "security_scope":"fixed_local_actions_no_unapproved_external_writes",
            "service_health":units,"receipts":receipt_data,"backup":backup_data,
            "audit_integrity_ok":audit_ok,"attention_required":issues}

if __name__=="__main__":
    print(json.dumps(health(),ensure_ascii=False))
