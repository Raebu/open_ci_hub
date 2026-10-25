"""Sanitised, read-only Open CI Hub background mission dashboard data."""
from __future__ import annotations
import sqlite3
from pathlib import Path

from autonomy_core import admit_local, sample_host

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".local" / "autonomy" / "autonomy.sqlite3"

def summary(db_path: Path = STATE, *, metrics=sample_host):
    data = {"configured": False, "running": 0, "queued": 0, "failed": 0,
            "paused": 0, "missions": [], "execution": "owner-approved-fixed-tasks"}
    safe_host = metrics()
    eligible, reason = admit_local(safe_host)
    data["admission"] = {"eligible": eligible, "reason": reason,
                          "memory_available_mb": safe_host.get("mem_available_mb"),
                          "load_per_core": safe_host.get("load_per_core")}
    file = Path(db_path)
    if file.is_symlink() or not file.is_file():
        return data
    with sqlite3.connect(f"file:{file}?mode=ro", uri=True, timeout=2) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT id,status,updated FROM missions "
                          "ORDER BY updated DESC LIMIT 25").fetchall()
        data["missions"] = [{"id": r["id"], "status": r["status"],
                             "updated": r["updated"]} for r in rows]
        data["configured"] = True
        for row in rows:
            if row["status"] in ("running", "queued", "failed", "paused"):
                data[row["status"]] += 1
    return data
