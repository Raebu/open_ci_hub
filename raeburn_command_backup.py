#!/usr/bin/env python3
"""Owner-private SQLite online snapshots on separate Razer internal storage.

No live DB replacement; verifies integrity + audit chain independently.
Local resilience only, not encrypted offsite disaster recovery.
"""
from __future__ import annotations
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid

from raeburn_command_centre import DB as SOURCE
DEST=Path.home()/".local"/"share"/"open-ci-hub"/"command-backups"
FILE=re.compile(r"^command-\d{8}T\d{6}-[0-9a-f]{10}\.sqlite3$")
KEEP=14

def _directory():
    if DEST.is_symlink():raise PermissionError("Symlinked backup destination refused")
    DEST.mkdir(parents=True,exist_ok=True,mode=0o700)
    st=DEST.stat()
    if st.st_uid!=os.getuid():raise PermissionError("Backup destination owner mismatch")
    DEST.chmod(0o700)

def _source():
    if SOURCE.is_symlink() or not SOURCE.is_file():
        raise PermissionError("Canonical mission database unavailable")
    st=SOURCE.stat()
    if st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o600:
        raise PermissionError("Mission source permissions invalid")

def verify(file):
    """Read-only independent restore simulation; original DB is untouched."""
    file=Path(file)
    if file.is_symlink() or not file.is_file():raise PermissionError("Invalid backup file")
    with closing(sqlite3.connect("file:"+str(file)+"?mode=ro",uri=True,timeout=8)) as db:
        db.row_factory=sqlite3.Row
        valid=db.execute("PRAGMA integrity_check").fetchone()[0]
        if valid!="ok":raise RuntimeError("SQLite integrity failed")
        missions=db.execute("SELECT count(*) FROM missions").fetchone()[0]
        steps=db.execute("SELECT count(*) FROM steps").fetchone()[0]
        audit=db.execute("SELECT mission,event,detail,at,previous_hash,event_hash FROM audit ORDER BY seq").fetchall()
    previous="0"*64
    for entry in audit:
        wire=json.dumps([entry["mission"],entry["event"],entry["detail"],entry["at"],previous],
                        ensure_ascii=True,separators=(",",":"))
        if (entry["previous_hash"]!=previous or
                hashlib.sha256(wire.encode()).hexdigest()!=entry["event_hash"]):
            raise RuntimeError("Audit hash chain failed")
        previous=entry["event_hash"]
    return {"sqlite_integrity":"ok","audit_chain":True,
            "missions":missions,"steps":steps,"audit_entries":len(audit)}

def backup():
    _source();_directory()
    basename="command-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")+"-"+uuid.uuid4().hex[:10]+".sqlite3"
    temporary=DEST/("."+basename+".tmp")
    output=DEST/basename
    fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,"O_NOFOLLOW",0),0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect("file:"+str(SOURCE)+"?mode=ro",uri=True,timeout=10)) as src:
            with closing(sqlite3.connect(str(temporary),timeout=10)) as target:
                src.backup(target)
        receipt=verify(temporary)
        size=temporary.stat().st_size
        digest=hashlib.sha256(temporary.read_bytes()).hexdigest()
        os.chmod(temporary,0o600)
        os.replace(temporary,output)
        manifest={"name":basename,"created_utc":datetime.now(timezone.utc).isoformat(),
                  "sha256":digest,"bytes":size,**receipt}
        metadata=DEST/(basename+".json")
        f=os.open(metadata,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,"O_NOFOLLOW",0),0o600)
        with os.fdopen(f,"w",encoding="utf8") as stream:
            json.dump(manifest,stream,sort_keys=True)
            stream.write("\n");stream.flush();os.fsync(stream.fileno())
        # Retain only 14 newest matching snapshot families, no arbitrary deletion.
        files=sorted((x for x in DEST.iterdir() if FILE.fullmatch(x.name)
                      and not x.is_symlink()),reverse=True)
        for stale in files[KEEP:]:
            stale.unlink()
            info=DEST/(stale.name+".json")
            if info.is_file() and not info.is_symlink():info.unlink()
        return {"status":"verified_local_backup","filename":basename,
                "digest":digest,"receipts":receipt,"retained":min(len(files),KEEP),
                "offsite_verified":False}
    finally:
        if temporary.exists():temporary.unlink()

if __name__=="__main__":
    print(json.dumps(backup()))
