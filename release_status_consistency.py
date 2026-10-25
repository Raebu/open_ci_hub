"""Fail-closed check for contradictory external GitHub commit-status publishers.

An authenticated provider job success is necessary but does not override a
required independent status context that is failed, superseded or incomplete.
This checks release eligibility only; it NEVER posts statuses or repairs the
external status publisher on the user's PC.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re


def _stamp(value):
    if not isinstance(value, str):
        raise PermissionError("Missing trustworthy provider timestamp.")
    try:
        out = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise PermissionError("Invalid provider timestamp.") from None
    if out.tzinfo is None:
        raise PermissionError("Unzoned provider timestamp.")
    return out.astimezone(timezone.utc)


def verify_github_release_statuses(*, api, repository, sha, run,
                                   required_context, trusted_publisher):
    if (not isinstance(required_context, str)
        or not 1 <= len(required_context) <= 90
        or not isinstance(trusted_publisher, str)
        or not re.fullmatch(r"[A-Za-z0-9_.\[\]-]{1,90}", trusted_publisher)):
        raise PermissionError("Explicit required context and publisher required.")
    if (not isinstance(run, dict) or run.get("status") != "completed"
        or run.get("conclusion") != "success" or run.get("head_sha") != sha):
        raise PermissionError("Native workflow is not complete for exact SHA.")
    finished = _stamp(run.get("updated_at"))
    history = api("gh", "repos/" + repository + "/commits/" + sha +
                  "/statuses?per_page=100")
    if not isinstance(history, list) or not 1 <= len(history) < 100:
        raise PermissionError("Commit status history missing or incomplete.")
    scoped = [s for s in history if isinstance(s, dict)
              and s.get("context") == required_context]
    if not scoped:
        raise PermissionError("Required external CI status context not found.")
    latest = max(scoped, key=lambda s: _stamp(s.get("created_at")))
    if (latest.get("state") != "success"
        or (latest.get("creator") or {}).get("login", "").casefold()
            != trusted_publisher.casefold()):
        raise PermissionError("Required external CI check failed or wrong publisher.")
    description = latest.get("description")
    if (not isinstance(description, str)
        or not description.strip()
        or re.search(r"supersed|skipp|not[ -]?run|obsolete|cancel|timed?[ -]?out|unknown|pending",
                     description, re.IGNORECASE)):
        raise PermissionError("Required CI check is not evidence of execution.")
    status_created = _stamp(latest.get("created_at"))
    if status_created < finished:
        raise PermissionError("External CI status was green before native CI finished.")
    return {"context": required_context, "publisher": trusted_publisher,
            "state": "success", "created_at": latest["created_at"],
            "native_finished_at": run["updated_at"]}
