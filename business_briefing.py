"""Human-readable non-sensitive execution briefing from canonical source pointers.

A briefing is NOT another CRM, a bid submission, a verified revenue figure or
an independent CI receipt. No untrusted issue/body text becomes instructions.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

ALLOWED_STAGES=frozenset((
    "dirty_checkout_do_not_modify","non_main_checkout","origin_mismatch",
    "checkout_unavailable","git_source_unavailable","release_gate_required",
    "protected_read_only"
))


def build(bridge):
    lines=["# Raeburn business execution — source-backed briefing",
           "",
           "Produced: "+datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "",
           "**Operating phase:** parallel shadow. The five ChatGPT engines continue.",
           "This scanner cannot send email, submit tender forms, merge code, deploy",
           "sites, sign contracts or make payments. No discovered lead counts as a sale.",
           ""]
    with bridge.db() as db:
        repos=db.execute(
            "SELECT source_ref,stage,detail FROM work "
            "WHERE kind='repository' ORDER BY lane,source_ref"
        ).fetchall()
        issues=db.execute(
            "SELECT source_ref,stage,detail FROM work "
            "WHERE kind='github_issue' ORDER BY source_ref LIMIT 30"
        ).fetchall()
        ci=db.execute(
            "SELECT source_ref,stage,detail FROM work WHERE kind='ci_health' "
            "ORDER BY source_ref"
        ).fetchall()
        prs=db.execute(
            "SELECT source_ref,stage,detail FROM work WHERE kind='pull_request' "
            "ORDER BY source_ref LIMIT 15"
        ).fetchall()
        leads=db.execute(
            "SELECT stage,detail FROM work WHERE kind='lead_review' "
            "ORDER BY source_ref LIMIT 30"
        ).fetchall()
        pointers=db.execute(
            "SELECT lane,source_ref,stage,detail FROM work "
            "WHERE kind IN ('funding','partners') ORDER BY lane,source_ref"
        ).fetchall()
        health=db.execute(
            "SELECT stage,detail FROM work WHERE kind='source_health' "
            "ORDER BY source_ref"
        ).fetchall()
        run=db.execute(
            "SELECT outcomes FROM runs WHERE status IN ('observed','degraded') "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    lines+=["## Product and repository completion",""]
    if not repos:
        lines.append("No repository readback yet.")
    for repo in repos:
        if repo["stage"] not in ALLOWED_STAGES:
            continue
        data=json.loads(repo["detail"])
        label=repo["source_ref"]
        lines.append(f"- **{label}**: {repo['stage']}; "
                     f"commit {data.get('commit') or 'unverified'}; "
                     "next: inspect trusted CI evidence, release acceptance and "
                     "any local uncommitted work before modifying anything.")
    lines+=["","### GitHub CI and review gates",""]
    for entry in ci:
        data=json.loads(entry["detail"])
        lines.append(f"- {entry['source_ref']}: {entry['stage']}; "
                     "reported GitHub CI status is not production acceptance.")
    for entry in prs:
        data=json.loads(entry["detail"])
        url=data.get("url","")
        if url.startswith("https://github.com/"):
            lines.append(f"- Review {entry['source_ref']}: {entry['stage']}; {url}")
    lines+=["","### Open GitHub issue intake (not merged, not repaired)",""]
    for issue in issues[:18]:
        data=json.loads(issue["detail"])
        url=data.get("url","")
        if url.startswith("https://github.com/"):
            lines.append(f"- {issue['source_ref']}: {issue['stage']}; {url}")
    lines+=["","## Direct paying clients and lead finder",""]
    outcome=json.loads(run["outcomes"]) if run else {}
    counts=outcome.get("leads",{})
    if counts:
        lines.append(
            f"- Existing source: {counts.get('companies',0)} companies; "
            f"{counts.get('contacts',0)} contacts; "
            f"{counts.get('enriched',0)} enriched companies; "
            f"{counts.get('ch_linked',0)} Companies House matches."
        )
    lines.append("- **Outreach remains in the existing ChatGPT sales engine.** "
                 "Check all inbox replies, suppressions, source/legal basis "
                 "and sender deliverability first. Do not send a duplicate campaign.")
    lines.append(f"- Shortlisted for additional independent provenance and PECR review: "
                 f"{len(leads)} cached source references (not send-ready).")
    lines+=["","## Tenders, funding and partnership qualification",""]
    for row in pointers:
        data=json.loads(row["detail"])
        ext=data.get("url","")
        source=ext if ext.startswith("https://") else "official source not yet verified"
        lines.append(f"- **{row['source_ref']}** ({row['lane']}): {row['stage']}; "
                     f"{source}; next: {data.get('next_gate','verify source')}.")
    lines+=["","## Canonical source freshness and COO exceptions",""]
    for row in health:
        data=json.loads(row["detail"])
        lines.append(f"- {data.get('source','external connector')}: {row['stage']}")
    lines.append("- The native Google Sheet, Google inboxes, Resend, bid portals "
                 "and CRM remain the sources of truth. This briefing is not a replacement.")
    lines.append("- Existing customer money/contract/deployment claims remain "
                 "unverified until authenticated provider readback.")
    lines+=["","## Migration acceptance (do not disable scheduled tasks yet)",""]
    for lane in bridge.readiness()["lanes"]:
        lines.append(f"- {lane['lane']}: blocked until independently verified "
                     +", ".join(lane["unproven"]))
    lines.append("")
    text="\n".join(lines)
    if len(text.encode())>25000:
        raise ValueError("Briefing exceeds conservative size limit.")
    return text


def save(bridge,path=None):
    path=Path(path) if path is not None else bridge.state/"briefing.md"
    if path.is_symlink() or path.parent.is_symlink():
        raise PermissionError("Unsafe business briefing destination.")
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    content=build(bridge)
    fd,temporary=tempfile.mkstemp(prefix=".briefing-",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary,0o600)
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"path":str(path),"bytes":len(content.encode()),
            "executed_external_actions":0}
