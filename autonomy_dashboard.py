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
    # Only public capability/health flags. Never expose session cookies,
    # local host paths, device tokens or browser artifacts on mobile.
    from virtual_desktop import status as display_status
    x11 = display_status()
    data["private_display"] = {
        "active": bool(x11.get("active") and x11.get("socket_present")),
        "driver_ready": bool(x11.get("binary_available")),
        "verified_access": "private_X11_cookie_required"}
    data["login_session_cache"] = {"enabled": False,
        "available": True,
        "reason": "per_site_owner_opt_in_required"}
    data["cloud_cost"] = {"policy": "deny_unknown_or_positive_cost"}
    file = Path(db_path)
    if file.is_symlink() or not file.is_file():
        return data
    with sqlite3.connect(f"file:{file}?mode=ro", uri=True, timeout=2) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT id,status,updated FROM missions "
                          "ORDER BY updated DESC LIMIT 25").fetchall()
        # Aggregate progress using statuses only, never mission URLs,
        # browser content, logins or task payloads in a mobile response.
        progress = db.execute(
            "SELECT mission,COUNT(*) AS total,"
            "SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS done,"
            "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,"
            "SUM(CASE WHEN evidence_sha256 IS NOT NULL THEN 1 ELSE 0 END) AS receipts "
            "FROM steps GROUP BY mission").fetchall()
        by_mission = {p["mission"]: dict(p) for p in progress}
        data["missions"] = [
            {"id": r["id"], "status": r["status"], "updated": r["updated"],
             "steps_total": by_mission.get(r["id"], {}).get("total", 0),
             "steps_completed": by_mission.get(r["id"], {}).get("done", 0),
             "evidence_receipts": by_mission.get(r["id"], {}).get("receipts", 0)}
            for r in rows]
        data["configured"] = True
        for row in rows:
            if row["status"] in ("running", "queued", "failed", "paused"):
                data[row["status"]] += 1
    return data
