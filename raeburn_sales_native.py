#!/usr/bin/env python3
"""Native Raeburn direct-client acquisition and ChatGPT handoff.

A real Razer/host-side operational lane inside the existing Open CI Hub
business-execution systemd timer. Does not create a second scheduler, tracker,
mail sender or LinkedIn publisher. Source-of-truth remains canonical lead
finder, inbox, email provider, outbox and business development sheet.

The report is a PRIVACY-SAFE, SHORT-LIVED working handoff to the existing
ChatGPT Sales task, which can fetch it via Open CI Hub. It cannot push a
ChatGPT message or borrow ChatGPT OAuth in the background.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time

from business_execution import PROJECTS, STATE, CONFIG, read_config
from business_channels import ROOT_STATE

SCHEMA = "raeburn.native-client-acquisition.v1"
FEED = STATE / "native-client-acquisition.json"
DB = STATE / "native-client-acquisition.sqlite"
MAX_SIZE = 22_000
MAX_AGE = 3 * 3600
APPROVED_LANES = {"sales"}
SAMPLE_LIMIT = 12
MAX_EVENTS = 10


def utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _read_only(path: Path):
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError("Canonical source file unavailable.")
    resolved = path.resolve(strict=True)
    # Explicit read-only URI: never mutate canonical sources.
    connection = sqlite3.connect(
        "file:" + str(resolved) + "?mode=ro", uri=True, timeout=5
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return closing(connection)


def _private_root(state: Path):
    if state.is_symlink() or any(x.is_symlink() for x in state.parents if x.exists()):
        raise PermissionError("Symlinked native sales state refused.")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state, 0o700)
    return state


def _counts(db, table: str, column: str):
    if table not in {"companies", "contacts", "job_runs"} or (
        column not in {"status", "review_status"}
    ):
        raise ValueError("Unapproved source grouping.")
    return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def _bounded_score(value):
    if value is None:
        return None
    score=float(value)
    return max(0.0,min(100.0,score)) if math.isfinite(score) else None


def lead_inspection(source: Path):
    """Read canonical lead finder without transferring names or addresses."""
    try:
        with _read_only(source) as db:
            count_companies = _counts(db, "companies", "status")
            count_contacts = _counts(db, "contacts", "status")
            run_count = _counts(db, "job_runs", "status")
            enrich = db.execute(
                "SELECT COUNT(*) FROM companies WHERE status='enriched'"
            ).fetchone()[0]
            companies_house = db.execute(
                "SELECT COUNT(*) FROM companies WHERE company_number IS NOT NULL "
                "AND company_number!=''"
            ).fetchone()[0]
            scored = db.execute(
                "SELECT COUNT(*) FROM companies WHERE company_fit_score IS NOT NULL"
            ).fetchone()[0]
            needs_review = db.execute(
                "SELECT COUNT(*) FROM companies WHERE "
                "COALESCE(needs_manual_validation,1)!=0 "
                "OR COALESCE(review_status,'') NOT IN ('approved','validated','verified')"
            ).fetchone()[0]
            sourced_contacts = db.execute(
                "SELECT COUNT(*) FROM contacts WHERE source_url IS NOT NULL "
                "AND source_url!=''"
            ).fetchone()[0]
            # IDs and numeric signals only, not names, email addresses,
            # personal details, full user-provided text or raw source URLs.
            sample = db.execute(
                "SELECT id, company_fit_score, contact_quality_score, "
                "CASE WHEN company_number IS NOT NULL AND company_number!='' "
                "THEN 1 ELSE 0 END AS house_match, "
                "CASE WHEN source_url IS NOT NULL AND source_url!='' "
                "THEN 1 ELSE 0 END AS has_source "
                "FROM companies WHERE status IN ('discovered','enriched') "
                "ORDER BY COALESCE(company_fit_score,0) DESC, id LIMIT ?",
                (SAMPLE_LIMIT,),
            ).fetchall()
            return {
                "state": "read_only",
                "companies": count_companies,
                "contacts": count_contacts,
                "enriched": enrich,
                "companies_house_matched": companies_house,
                "scored": scored,
                "needs_manual_review": needs_review,
                "contacts_with_source_url": sourced_contacts,
                "job_runs": run_count,
                "verified_eligible_to_email": 0,
                "review_queue": [
                    {
                        "source_ref": f"find_rec_firms:company:{int(row['id'])}",
                        "company_id": int(row["id"]),
                        "fit_score": _bounded_score(row["company_fit_score"]),
                        "contact_quality_score": _bounded_score(row["contact_quality_score"]),
                        "companies_house_matched": bool(row["house_match"]),
                        "has_public_source": bool(row["has_source"]),
                        "stage": "source_and_legal_review_required",
                    }
                    for row in sample
                ],
            }
    except (OSError, sqlite3.Error, ValueError, OverflowError):
        return {"state": "unavailable", "companies": None, "contacts": None,
                "verified_eligible_to_email": 0, "review_queue": []}


def communication_inspection(source: Path, now: float):
    """Verify mail readback and conservatively record held contacts."""
    unavailable = {
        "state": "no_independent_native_inbox_readback",
        "all_approved_business_inboxes_fresh": False,
        "verified_human_replies": None,
        "manual_reply_review_holds": None,
        "provider_suppression_holds": None,
        "native_provider_message_counts": {},
        "accounts": [],
    }
    try:
        with _read_only(source) as db:
            accounts = [
                {
                    "alias": str(row["account"])[:50],
                    "state": str(row["state"])[:70],
                    "fresh": (
                        row["state"] == "authenticated_read_only"
                        and 0 <= now - float(row["last_checked"]) <= 900
                    ),
                }
                for row in db.execute(
                    "SELECT account,state,last_checked FROM sync "
                    "WHERE account IN ('raeburn-primary','raeburn-contact','raeburn-resend') "
                    "ORDER BY account"
                )
            ]
            states = {
                str(row["classification"]): int(row["n"])
                for row in db.execute(
                    "SELECT classification,COUNT(*) AS n "
                    "FROM event GROUP BY classification"
                )
            }
            provider_holds = db.execute(
                "SELECT COUNT(*) FROM hold WHERE reason "
                "IN ('provider_suppression','provider_delivery_or_suppression')"
            ).fetchone()[0]
            reply_holds = db.execute(
                "SELECT COUNT(*) FROM hold WHERE reason='inbound_requires_reply_review'"
            ).fetchone()[0]
            fresh = (
                len(accounts) == 3
                and all(item["fresh"] for item in accounts)
            )
            return {
                "state": "authenticated_readback_fresh" if fresh
                         else "local_provider_authorisation_incomplete",
                "all_approved_business_inboxes_fresh": fresh,
                "verified_human_replies": None,
                "manual_reply_review_holds": int(reply_holds),
                "provider_suppression_holds": int(provider_holds),
                "native_provider_message_counts": states,
                "accounts": accounts,
            }
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return unavailable


def queue_inspection(source: Path):
    """Never decrypt/print prospect PII or count provider accepted as delivered."""
    default = {"state": "unavailable", "email_queue": {}, "linkedin_queue": {},
               "accepted_not_delivery_proof": 0,
               "uncertain_sends_require_reconciliation": 0}
    try:
        with _read_only(source) as db:
            mail = {
                str(row["state"]): int(row["n"])
                for row in db.execute("SELECT state,COUNT(*) AS n "
                                      "FROM job GROUP BY state")
            }
            social = {
                str(row["stage"]): int(row["n"])
                for row in db.execute("SELECT stage,COUNT(*) AS n "
                                      "FROM social_job GROUP BY stage")
            }
            return {
                "state": "read_only",
                "email_queue": mail,
                "linkedin_queue": social,
                "accepted_not_delivery_proof": mail.get("accepted_pending_delivery", 0),
                "uncertain_sends_require_reconciliation": mail.get(
                    "dispatch_uncertain", 0),
            }
    except (OSError, sqlite3.Error):
        return default


def sheet_snapshot(source: Path, now: float):
    """My Tasks is a bounded pointer, NOT a verified pipeline receipt."""
    invalid = {"state": "unavailable", "row_count": 0}
    try:
        if source.is_symlink() or not source.is_file() or source.stat().st_size > 65536:
            return invalid
        if source.stat().st_mode & 0o077:
            return {"state": "unsafe", "row_count": 0}
        raw = json.loads(source.read_text(encoding="utf8"))
        if (raw.get("schema") != "raeburn.canonical-sheet-cache.v1"
            or raw.get("tab") != "My Tasks"
            or not isinstance(raw.get("rows"), list)
            or len(raw["rows"]) > 80):
            return {"state": "invalid", "row_count": 0}
        observed = datetime.fromisoformat(
            raw["observed_at"].replace("Z", "+00:00"))
        age = now - observed.timestamp()
        if observed.tzinfo is None or not 0 <= age <= 86400:
            return {"state": "expired", "row_count": 0}
        sales_rows = [
            entry["row"] for entry in raw["rows"]
            if isinstance(entry,dict) and entry.get("lane") == "sales"
            and type(entry.get("row")) is int
        ]
        return {"state": "fresh_read_only_snapshot", "row_count":len(sales_rows),
                "source_age_seconds":int(age)}
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return {"state": "invalid", "row_count": 0}


def plan_next_actions(lead, communications, queues, sheet):
    actions = []
    def add(priority, code, rationale, scope):
        actions.append({"priority":priority,"action":code,
                        "why":rationale,"system":scope})
    if queues["uncertain_sends_require_reconciliation"]:
        add("critical", "reconcile_uncertain_provider_send",
            "Unknown provider outcome must be checked before any retry.",
            "ChatGPT Gmail / Resend + native outbox")
    if communications.get("manual_reply_review_holds"):
        add("critical", "review_unclassified_inbound_and_stop_chasers",
            "Incoming mail has been observed; human intent is not verified.",
            "ChatGPT business inbox threads")
    if communications.get("provider_suppression_holds"):
        add("high", "reconcile_suppression_across_channels",
            "Provider holds must be honoured across all senders.",
            "canonical pipeline and all authorised mailboxes")
    if not communications["all_approved_business_inboxes_fresh"]:
        add("high", "use_connected_chatgpt_outreach_until_native_oauth_ready",
            "The Razer cannot independently verify every business inbox.",
            "existing Raeburn Direct Client Acquisition ChatGPT task")
    if sheet["state"] != "fresh_read_only_snapshot":
        add("high", "refresh_canonical_task_readback",
            "The canonical My Tasks source is unavailable or stale.",
            "existing Google Sheet through authorised connector")
    if lead.get("state") == "read_only" and lead.get("review_queue"):
        add("normal", "qualify_lead_finder_company_sources",
            "Records need current role, source, domain, PECR and dedupe checks.",
            "existing Find_rec_firms / canonical BD pipeline")
    if not queues["email_queue"].get("queued"):
        add("normal", "avoid_reclassifying_unverified_leads_as_send_ready",
            "There is no canonical, legally screened and handed-off native send queue.",
            "existing ChatGPT Sales engine")
    if lead["state"] != "read_only":
        add("high", "restore_canonical_lead_database_readback",
            "The original lead finder cannot be verified.",
            "Find_rec_firms")
    order = {"critical":0,"high":1,"normal":2}
    return sorted(actions,key=lambda x:(order[x["priority"]],x["action"]))[:10]


def safe_metric_snapshot(lead, communications, queues, sheet):
    return {
        "lead_source": {
            "state": lead["state"], "companies":lead.get("companies"),
            "contacts":lead.get("contacts"),
            "enriched": lead.get("enriched"),
            "scored": lead.get("scored"),
            "needs_manual_review":lead.get("needs_manual_review"),
            "verified_eligible_to_email":lead.get("verified_eligible_to_email"),
            "review_queue":lead.get("review_queue",[]),
        },
        "mailbox": communications,
        "native_dispatch": queues,
        "canonical_sheet": sheet,
    }


def _change_signature(metric):
    # Ages change every minute, but time passing is not a new sales event.
    stable=json.loads(json.dumps(metric,allow_nan=False))
    stable.get("canonical_sheet",{}).pop("source_age_seconds",None)
    return hashlib.sha256(
        json.dumps(stable,sort_keys=True,separators=(",",":"),
                   allow_nan=False).encode("utf8")
    ).hexdigest()


class SalesFeed:
    def __init__(self, state=STATE,*,clock=time.time):
        self.state=_private_root(Path(state))
        self.dbpath=self.state/"native-client-acquisition.sqlite"
        self.path=self.state/"native-client-acquisition.json"
        self.clock=clock
        if self.dbpath.is_symlink() or self.path.is_symlink():
            raise PermissionError("Unsafe native sales handoff file.")
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS snapshot(
              singleton INTEGER PRIMARY KEY CHECK(singleton=1),
              signature TEXT NOT NULL,updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS event(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              signature TEXT NOT NULL,observed_at REAL NOT NULL,
              status TEXT NOT NULL);
            """)
        os.chmod(self.dbpath,0o600)

    def connect(self):
        connection=sqlite3.connect(str(self.dbpath),isolation_level=None,
                                   timeout=10)
        connection.row_factory=sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        return closing(connection)

    def build(self,*,config=None):
        now=self.clock()
        cfg=config if config is not None else read_config()
        rel=Path(cfg["lead_db"])
        if rel.is_absolute() or ".." in rel.parts:
            raise PermissionError("Lead DB path escaped the approved project root.")
        lead=lead_inspection(PROJECTS / rel)
        mailbox=communication_inspection(
            self.state/"channel-readback.sqlite",now)
        outbox=queue_inspection(self.state/"outbound.sqlite")
        sheet=sheet_snapshot(self.state/"my-tasks-source.json",now)
        metric=safe_metric_snapshot(lead,mailbox,outbox,sheet)
        signature=_change_signature(metric)
        actions=plan_next_actions(lead,mailbox,outbox,sheet)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current=db.execute("SELECT signature FROM snapshot WHERE singleton=1"
                              ).fetchone()
            changed=not current or current["signature"]!=signature
            if changed:
                db.execute(
                    "INSERT INTO snapshot(singleton,signature,updated_at) VALUES(1,?,?) "
                    "ON CONFLICT(singleton) DO UPDATE SET signature=excluded.signature,"
                    "updated_at=excluded.updated_at",
                    (signature,now))
                db.execute("INSERT INTO event(signature,observed_at,status) VALUES(?,?,?)",
                           (signature,now,"source_state_changed"))
            latest=db.execute(
                "SELECT COALESCE(MAX(id),0) FROM event"
            ).fetchone()[0]
            count=db.execute("SELECT COUNT(*) FROM event").fetchone()[0]
            db.commit()
        state=("degraded" if mailbox["state"]!="authenticated_readback_fresh"
               or lead["state"]!="read_only" else "observed")
        report={
            "schema":SCHEMA, "at":utc(now),
            "owner":"Razer native Open CI Hub",
            "runner":"existing_open_ci_hub_business_execution_systemd_timer",
            "source_status":state,
            "source_coverage": {
                "lead_database":lead["state"]=="read_only",
                "all_business_inboxes_fresh":mailbox[
                    "all_approved_business_inboxes_fresh"],
                "canonical_tasks_fresh":sheet["state"]=="fresh_read_only_snapshot",
                "canonical_sales_contacts_reconciled":False,
            },
            "metrics":metric,"recommended_next_actions":actions,
            "material_change":bool(changed),"event_cursor":int(latest),
            "event_count":int(count),
            "verified_direct_client_emails_sent_today":None,
            "verified_human_replies_today":None,
            "verified_sales_booked_today":None,
            "verified_cash_collected_today":None,
            "native_accepted_not_confirmed_delivered":int(
                outbox["accepted_not_delivery_proof"]),
            "no_duplicate_campaign_started":True,
            "requires_chatgpt_connector_pull":True,
            "autonomous_native_email_ready":False,
            "native_email_pending_gates":[
                "full cross-provider canonical ownership and suppression reconciliation",
                "independent sender OAuth and verified provider write receipt",
            ],
            "notes":[
                "This is a native Razer source-backed handoff, not a ChatGPT task run.",
                "Connected ChatGPT Gmail/Resend can continue authorised selling.",
                "Actual new outbound totals cannot be inferred from lead or queue counts.",
                "Review original inbox and canonical BD sheet before any external send.",
                "No direct ChatGPT task push API: existing Sales task pulls this report.",
            ],
        }
        self._write_report(report)
        return report

    def _write_report(self,report):
        data=json.dumps(report,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)
        if len(data.encode())>MAX_SIZE:
            raise ValueError("Native sales feed size exceeds bounded limit.")
        fd,tmp=tempfile.mkstemp(prefix=".native-sales-",dir=self.state)
        try:
            with os.fdopen(fd,"w",encoding="utf8") as stream:
                stream.write(data+"\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(tmp,0o600)
            os.replace(tmp,self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def handoff(self):
        if not self.path.exists() or self.path.is_symlink() or (
                self.path.stat().st_mode&0o077
            ) or self.path.stat().st_size>MAX_SIZE:
            return {"schema":SCHEMA,"status":"not_available","feed_fresh":False}
        try:
            payload=json.loads(self.path.read_text(encoding="utf8"))
            if payload.get("schema")!=SCHEMA:
                raise ValueError("Wrong handoff schema.")
            stamp=datetime.fromisoformat(payload["at"].replace("Z","+00:00"))
            age=self.clock()-stamp.timestamp()
            if stamp.tzinfo is None or not 0<=age<=MAX_AGE:
                return {"schema":SCHEMA,"status":"stale","feed_fresh":False}
            return {**payload,"feed_fresh":True,
                    "age_seconds":int(age)}
        except (OSError,ValueError,KeyError,TypeError,OverflowError):
            return {"schema":SCHEMA,"status":"invalid","feed_fresh":False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("run","handoff","status"))
    args=parser.parse_args(argv)
    try:
        feed=SalesFeed()
        if args.action=="run":
            result=feed.build()
            # Default native service log is bounded and contains no PII.
            out={"schema":SCHEMA,"status":result["source_status"],
                 "event_cursor":result["event_cursor"],
                 "material_change":result["material_change"],
                 "outreach_sent_today":result["verified_direct_client_emails_sent_today"],
                 "source_coverage":result["source_coverage"],
                 "recommended_actions":len(result["recommended_next_actions"])}
        elif args.action=="status":
            report=feed.handoff()
            out={"schema":SCHEMA,"status":report.get("source_status",report.get("status")),
                 "feed_fresh":report.get("feed_fresh",False),
                 "event_cursor":report.get("event_cursor",0),
                 "source_coverage":report.get("source_coverage",{})}
        else:
            out=feed.handoff()
        print(json.dumps(out,indent=2,sort_keys=True))
        return 0
    except (OSError,PermissionError,ValueError,sqlite3.Error) as error:
        print("Native client-acquisition handoff refused: "+type(error).__name__,
              file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
