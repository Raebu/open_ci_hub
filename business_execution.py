#!/usr/bin/env python3
"""Raeburn business execution bridge: safe shadow migration of ChatGPT engines.

Executes read-only source reconciliation and durable evidence planning. Source
systems (My Tasks, BD Pipeline, Lead Finder DB, GitHub) remain canonical.
This program cannot send emails, change customer financial data, submit bids,
modify websites, execute project source, merge PRs or disable ChatGPT tasks.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
PROJECTS = ROOT.parent
STATE = ROOT / ".local" / "business-execution"
CONFIG = ROOT / ".local" / "business-execution" / "config.json"
SCHEMA = "raeburn.business-execution.v1"
LANES = ("sales", "funding", "product", "portfolio", "coo")
MODES = {"shadow", "gated"}
SAFE = re.compile(r"^[A-Za-z0-9_./:-]{1,240}$")
IDENT = re.compile(r"^[A-Za-z0-9_.:@/-]{1,240}$")
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
AUTOMATION_KEYS = frozenset(("sales","funding","product","portfolio","coo"))
MAX_ITEMS = 150
MAX_SOURCE_SIZE = 200000
CASH_GATES = ("provider_receipt", "bank_or_merchant_settlement_readback")
MIGRATION_GATES = {
    "sales": ("inbox_readback","suppression_readback","sent_message_readback",
              "canonical_sheet_readback","real_reply_outcome"),
    "funding": ("portal_source_readback","eligibility_review","submission_receipt",
                "canonical_sheet_readback"),
    "product": ("isolated_ci_receipt","trusted_review","deployment_readback",
                "customer_acceptance"),
    "portfolio": ("partner_reply_readback","regulatory_claims_review",
                  "canonical_sheet_readback"),
    "coo": ("cross_engine_reconciliation","personal_obligations_coverage",
            "canonical_sheet_readback","escalation_delivery"),
}


def digest(v):
    return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(",",":"),
                                  ensure_ascii=True,allow_nan=False).encode()).hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def private_path(folder):
    if folder.is_symlink() or any(p.is_symlink() for p in (folder,*folder.parents) if p.exists()):
        raise PermissionError("Refusing symlinked state location.")
    folder.mkdir(mode=0o700,parents=True,exist_ok=True)
    if os.name=="posix":
        os.chmod(folder,0o700)
    return folder


def safe_run(argv, *, cwd=None, timeout=10):
    """Only trusted, fixed read-only executables and argv; never shells."""
    if not argv or argv[0] not in ("git","gh") or len(argv)>16:
        raise ValueError("Read-only Git/GitHub command not approved.")
    if any(not isinstance(a,str) or len(a)>400 for a in argv):
        raise ValueError("Invalid command arguments.")
    try:
        env={**os.environ,"GH_PROMPT_DISABLED":"1","GIT_TERMINAL_PROMPT":"0"}
        proc=subprocess.run(argv,cwd=cwd,stdin=subprocess.DEVNULL,
                            capture_output=True,text=True,timeout=timeout,
                            check=False,env=env)
        return proc.stdout[:18000] if proc.returncode==0 else None
    except (OSError,subprocess.TimeoutExpired):
        return None


def validate_config(config):
    if not isinstance(config,dict) or set(config)!={
        "schema","mode","automations","canonical","repos","lead_db","partners","funding"
    } or config.get("schema")!=SCHEMA or config.get("mode") not in MODES:
        raise ValueError("Invalid business execution configuration.")
    a=config["automations"]
    if not isinstance(a,dict) or set(a)!=AUTOMATION_KEYS:
        raise ValueError("Exactly five existing automation IDs must be mapped.")
    if any(not re.fullmatch(r"[a-f0-9]{32}",x) for x in a.values()) or len(set(a.values()))!=5:
        raise ValueError("Ambiguous or unverified ChatGPT task mapping.")
    c=config["canonical"]
    if not isinstance(c,dict) or set(c)!={"my_tasks_url","bd_pipeline_url"}:
        raise ValueError("Existing canonical sheets must remain explicit.")
    if any(not isinstance(u,str) or not u.startswith("https://docs.google.com/spreadsheets/d/")
           for u in c.values()):
        raise ValueError("Unknown canonical spreadsheet.")
    repos=config["repos"]
    if not isinstance(repos,list) or not 1<=len(repos)<=20:
        raise ValueError("Provide 1–20 explicitly selected repositories.")
    seen=set()
    for item in repos:
        if not isinstance(item,dict) or set(item)!={"repo","path","lane","protected"}:
            raise ValueError("Unreviewed repository fields.")
        if not REPO.fullmatch(item["repo"]) or item["repo"] in seen:
            raise ValueError("Invalid or duplicated repository.")
        seen.add(item["repo"])
        if item["lane"] not in LANES or type(item["protected"]) is not bool:
            raise ValueError("Invalid project lane or protected status.")
        if not isinstance(item["path"],str) or not SAFE.fullmatch(item["path"]) or (
            item["path"].startswith("/") or ".." in item["path"].split("/")
        ):
            raise ValueError("Repository checkout path is outside the trusted root.")
    if not isinstance(config["lead_db"],str) or not SAFE.fullmatch(config["lead_db"]) or (
        config["lead_db"].startswith("/") or ".." in config["lead_db"].split("/")
    ):
        raise ValueError("Invalid lead source path.")
    for group in ("partners","funding"):
        data=config[group]
        if not isinstance(data,list) or len(data)>30:
            raise ValueError("Too many source pointers.")
        ids=set()
        for item in data:
            if not isinstance(item,dict) or set(item)!={"id","lane","url","stage","next_gate"}:
                raise ValueError("Source pointers must have exact fields.")
            if not IDENT.fullmatch(item["id"]) or item["id"] in ids:
                raise ValueError("Invalid or duplicate canonical source ID.")
            ids.add(item["id"])
            if item["lane"] not in ("funding","portfolio","sales") or not isinstance(
                    item["stage"],str) or len(item["stage"])>70 or not isinstance(
                    item["next_gate"],str) or len(item["next_gate"])>150:
                raise ValueError("Invalid source gate.")
            url=item["url"]
            if not isinstance(url,str) or (
                url and (urlparse(url).scheme!="https" or not urlparse(url).netloc)
            ):
                raise ValueError("Only grounded HTTPS source URLs.")
    return config


def read_config(path=CONFIG):
    p=Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_size>30000:
        raise PermissionError("Missing or unsafe reviewed config.")
    return validate_config(json.loads(p.read_text(encoding="utf8")))


class Bridge:
    def __init__(self, *, state=STATE, config=None, command=safe_run, clock=time.time):
        self.config=validate_config(config) if config is not None else read_config()
        self.state=private_path(Path(state))
        self.file=self.state/"control.sqlite"
        if self.file.is_symlink():
            raise PermissionError("Refusing symlinked operations database.")
        self.command=command
        self.clock=clock
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS work (
                    id TEXT PRIMARY KEY, lane TEXT NOT NULL, kind TEXT NOT NULL,
                    source_ref TEXT NOT NULL, stage TEXT NOT NULL,
                    evidence_sha256 TEXT NOT NULL, detail TEXT NOT NULL,
                    first_seen REAL NOT NULL, last_seen REAL NOT NULL,
                    changes INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS ix_work_lane ON work(lane,stage);
                CREATE TABLE IF NOT EXISTS event (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    work_id TEXT NOT NULL, old_stage TEXT, new_stage TEXT NOT NULL,
                    evidence_sha256 TEXT NOT NULL, created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, started REAL NOT NULL,
                    ended REAL, status TEXT NOT NULL, outcomes TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS acceptance (
                    lane TEXT NOT NULL, requirement TEXT NOT NULL,
                    receipt_ref TEXT NOT NULL, independently_verified INTEGER NOT NULL,
                    verified_at REAL NOT NULL,
                    PRIMARY KEY(lane,requirement)
                );
            """)
            db.execute("PRAGMA busy_timeout=5000")
        if os.name=="posix":
            os.chmod(self.file,0o600)

    def db(self):
        con=sqlite3.connect(str(self.file),timeout=10, isolation_level=None)
        con.row_factory=sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000")
        return closing(con)

    def observe(self,lane,kind,source_ref,stage,details):
        if lane not in LANES or not isinstance(source_ref,str) or len(source_ref)>400:
            raise ValueError("Invalid canonical source reference.")
        if not isinstance(stage,str) or len(stage)>80 or not isinstance(details,dict):
            raise ValueError("Invalid observation.")
        raw=json.dumps(details,sort_keys=True,separators=(",",":"),ensure_ascii=True)
        if len(raw)>2048:
            raise ValueError("No large or personal data in operations ledger.")
        ident=digest({"lane":lane,"kind":kind,"source":source_ref})[:32]
        sig=digest({"stage":stage,"details":details})
        now=self.clock()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            old=db.execute("SELECT stage,evidence_sha256 FROM work WHERE id=?",(ident,)).fetchone()
            if old is None:
                db.execute("INSERT INTO work(id,lane,kind,source_ref,stage,"
                           "evidence_sha256,detail,first_seen,last_seen) "
                           "VALUES(?,?,?,?,?,?,?,?,?)",
                           (ident,lane,kind,source_ref,stage,sig,raw,now,now))
            elif old["evidence_sha256"]!=sig:
                db.execute("UPDATE work SET stage=?,evidence_sha256=?,detail=?,"
                           "last_seen=?,changes=changes+1 WHERE id=?",
                           (stage,sig,raw,now,ident))
            else:
                db.execute("UPDATE work SET last_seen=? WHERE id=?",(now,ident))
            if old is None or old["evidence_sha256"]!=sig:
                db.execute("INSERT INTO event(work_id,old_stage,new_stage,"
                           "evidence_sha256,created) VALUES(?,?,?,?,?)",
                           (ident,old["stage"] if old else None,stage,sig,now))
            db.commit()
        return ident

    def repo_snapshots(self):
        output=[]
        for item in self.config["repos"]:
            repo=item["repo"]
            target=PROJECTS/item["path"]
            record={"repo":repo,"protected":item["protected"],
                    "local_path":item["path"]}
            if target.is_symlink() or not target.is_dir() or not (target/".git").exists():
                stage="checkout_unavailable"
                record["reason"]="Local trusted checkout unavailable"
            else:
                origin=self.command(["git","-C",str(target),"remote","get-url","origin"],timeout=5)
                name=self.command(["git","-C",str(target),"branch","--show-current"],timeout=5)
                head=self.command(["git","-C",str(target),"rev-parse","HEAD"],timeout=5)
                status=self.command(["git","-C",str(target),"status","--porcelain"],timeout=8)
                behind=self.command(["git","-C",str(target),"rev-list","--left-right",
                                      "--count","HEAD...@{upstream}"],timeout=5)
                trusted=(origin is not None and origin.strip() in (
                         "https://github.com/"+repo,
                         "https://github.com/"+repo+".git",
                         "git@github.com:"+repo+".git"
                     ))
                if not trusted:
                    stage="origin_mismatch"
                elif status is None or head is None or name is None:
                    stage="git_source_unavailable"
                elif status.strip():
                    stage="dirty_checkout_do_not_modify"
                elif name.strip()!="main":
                    stage="non_main_checkout"
                elif item["protected"]:
                    stage="protected_read_only"
                else:
                    stage="release_gate_required"
                record.update({"commit":head.strip()[:40] if head else None,
                               "branch":name.strip() if name else None,
                               "dirty":bool(status and status.strip()),
                               "origin_verified":trusted,
                               "ahead_behind":behind.strip() if behind else None})
            self.observe(item["lane"],"repository",repo,stage,record)
            output.append({"repo":repo,"stage":stage,
                           "commit":record.get("commit"),
                           "dirty":record.get("dirty"),
                           "protected":item["protected"]})
        return output

    def github_issue_snapshots(self,*, limit_per_repo=5):
        """Read issues only; untrusted titles never become execution instructions."""
        output=[]
        if not 1<=limit_per_repo<=10:
            raise ValueError("Issue limit too large.")
        for item in self.config["repos"]:
            repo=item["repo"]
            raw=self.command(["gh","issue","list","--repo",repo,"--state","open",
                              "--limit",str(limit_per_repo),"--json",
                              "number,title,url,updatedAt,labels"],timeout=12)
            if raw is None:
                self.observe(item["lane"],"source_health","github:"+repo,
                             "github_read_unavailable",{"repo":repo})
                continue
            try:
                issues=json.loads(raw)
            except (ValueError,TypeError):
                continue
            if not isinstance(issues,list):
                continue
            for issue in issues:
                number=issue.get("number")
                if type(number) is not int or number<1:
                    continue
                url=issue.get("url","")
                if url!=f"https://github.com/{repo}/issues/{number}":
                    continue
                labels=[str(v.get("name",""))[:55] for v in issue.get("labels",[])[:12]
                        if isinstance(v,dict) and isinstance(v.get("name"),str)]
                classification=("critical_triage" if any(
                    str(label).lower() in ("p0","security","critical","incident")
                    for label in labels) else "issue_needs_evidence")
                key=repo+"#"+str(number)
                self.observe(item["lane"],"github_issue",key,classification,
                             {"repo":repo,"issue":number,"url":url,
                              "protected":item["protected"],"labels":labels[:8],
                              "updated":str(issue.get("updatedAt",""))[:24]})
                output.append({"repo":repo,"issue":number,"stage":classification,"url":url})
        return output

    def ci_snapshots(self):
        """Read only official GitHub workflow/PR metadata; never merge/deploy."""
        outputs=[]
        for item in self.config["repos"]:
            repo=item["repo"]
            base=(PROJECTS/item["path"])
            sha=self.command(["git","-C",str(base),"rev-parse","HEAD"],timeout=5) if (
                base.is_dir() and not base.is_symlink()) else None
            observed_sha=sha.strip() if sha else None
            raw=self.command(["gh","run","list","--repo",repo,"--limit","3",
                              "--json","databaseId,headSha,status,conclusion,createdAt,workflowName"],
                             timeout=12)
            if raw is None:
                self.observe(item["lane"],"ci_health",repo,
                             "github_actions_read_unavailable",{"repo":repo})
                outputs.append({"repo":repo,"ci":"unavailable"})
                continue
            try:
                runs=json.loads(raw)
            except (ValueError,TypeError):
                runs=[]
            if not isinstance(runs,list):
                runs=[]
            current=next((x for x in runs if isinstance(x,dict) and
                          x.get("headSha")==observed_sha and observed_sha),None)
            if current is None:
                stage="ci_for_current_checkout_unverified"
            elif current.get("status")=="completed" and current.get("conclusion")=="success":
                stage="github_ci_reported_pass_release_gates_pending"
            elif current.get("status") in ("queued","in_progress","waiting","pending"):
                stage="github_ci_pending"
            else:
                stage="github_ci_not_green"
            record={"repo":repo,"local_head":observed_sha,
                    "workflow_run_id":current.get("databaseId") if current else None,
                    "workflow_state":current.get("status") if current else None,
                    "conclusion":current.get("conclusion") if current else None,
                    "github_run_read":True,
                    "not_release_approval":True}
            self.observe(item["lane"],"ci_health",repo,stage,record)
            outputs.append({"repo":repo,"ci":stage})
            raw_prs=self.command(["gh","pr","list","--repo",repo,"--state","open",
                                  "--limit","3","--json",
                                  "number,url,headRefName,baseRefName,isDraft"],
                                 timeout=12)
            if raw_prs is None:
                continue
            try:
                prs=json.loads(raw_prs)
            except (ValueError,TypeError):
                continue
            if not isinstance(prs,list):
                continue
            for pr in prs:
                if not isinstance(pr,dict) or type(pr.get("number")) is not int:
                    continue
                n=pr["number"]
                if pr.get("url")!=f"https://github.com/{repo}/pull/{n}":
                    continue
                stage=("draft_pr_review_only" if pr.get("isDraft") is True
                       else "open_pr_requires_trusted_checks_and_review")
                self.observe(item["lane"],"pull_request",repo+"!"+str(n),stage,
                             {"repo":repo,"pr":n,"url":pr["url"],
                              "base":str(pr.get("baseRefName",""))[:100],
                              "protected":item["protected"]})
        return outputs

    def lead_snapshots(self,*, cap=20):
        """Use existing Lead Finder read-only; no PII copied to logs or state."""
        p=PROJECTS/self.config["lead_db"]
        data={"source":"Find_rec_firms","status":"unavailable","counts":{},"review":[]}
        if p.is_symlink() or not p.is_file():
            self.observe("sales","source_health","lead_finder_db",
                         "source_unavailable",{"source":"Find_rec_firms"})
            return data
        try:
            with closing(sqlite3.connect(f"file:{p}?mode=ro",uri=True,timeout=4)) as db:
                db.row_factory=sqlite3.Row
                counts={}
                for tab in ("companies","contacts","job_runs"):
                    counts[tab]=db.execute("SELECT count(*) FROM "+tab).fetchone()[0]
                counts["enriched"]=db.execute(
                    "SELECT count(*) FROM companies WHERE status='enriched'"
                ).fetchone()[0]
                counts["ch_linked"]=db.execute(
                    "SELECT count(*) FROM companies WHERE company_number IS NOT NULL "
                    "AND company_number!=''").fetchone()[0]
                counts["scored"]=db.execute(
                    "SELECT count(*) FROM companies WHERE company_fit_score IS NOT NULL"
                ).fetchone()[0]
                counts["eligible_for_automatic_email"]=0
                rows=db.execute(
                    "SELECT id, status, company_fit_score, contact_quality_score,"
                    "company_number, source_url, review_status, "
                    "needs_manual_validation FROM companies "
                    "WHERE status IN ('enriched','discovered') "
                    "ORDER BY COALESCE(company_fit_score,0) DESC, id "
                    "LIMIT ?",(min(cap,50),)
                ).fetchall()
        except (sqlite3.Error,OSError):
            self.observe("sales","source_health","lead_finder_db",
                         "source_unavailable",{"source":"Find_rec_firms"})
            return data
        output=[]
        for row in rows:
            idval=int(row["id"])
            # No name, address, email, private phone or prospect biography
            # are imported into the operations ledger.
            facts={"source":"Find_rec_firms","company_id":idval,
                   "enrichment":row["status"],
                   "fit_score":row["company_fit_score"],
                   "quality_score":row["contact_quality_score"],
                   "companies_house_matched":bool(row["company_number"]),
                   "has_public_source":bool(row["source_url"]),
                   "review_status":str(row["review_status"] or "unreviewed")[:60]}
            stage="requires_provenance_and_pecr_review"
            self.observe("sales","lead_review",f"find_rec_firms:company:{idval}",
                         stage,facts)
            output.append({"company_id":idval,"stage":stage,
                           "fit_score":row["company_fit_score"]})
        data.update({"status":"read_only","counts":counts,"review":output})
        return data

    def sheet_snapshots(self):
        """Inspect a private, expiring authenticated-connector snapshot.

        The Google Sheet remains the system of record. It is not safe for
        this local service to reuse ChatGPT's OAuth session, and a snapshot
        is not proof that an external bid/email was sent or money received.
        """
        path=self.state/"my-tasks-source.json"
        absent={"status":"unavailable","rows":0,"source":"My Tasks"}
        if path.is_symlink() or not path.is_file() or path.stat().st_size>65536:
            self.observe("coo","source_health","my_tasks_connector",
                         "connector_snapshot_unavailable",absent)
            return absent
        if os.name=="posix" and (path.stat().st_mode & 0o077):
            self.observe("coo","source_health","my_tasks_connector",
                         "connector_snapshot_unsafe",{"source":"My Tasks"})
            return {"status":"unsafe","rows":0}
        try:
            data=json.loads(path.read_text(encoding="utf8"))
            required={"schema","spreadsheet_id","tab","observed_at","rows"}
            if not isinstance(data,dict) or set(data)!=required or (
                data["schema"]!="raeburn.canonical-sheet-cache.v1" or
                data["tab"]!="My Tasks" or
                not isinstance(data["observed_at"],str) or
                not isinstance(data["rows"],list) or len(data["rows"])>80
            ):
                raise ValueError("Invalid source snapshot envelope.")
            canonical=self.config["canonical"]["my_tasks_url"]
            if data["spreadsheet_id"]!=canonical.split("/d/",1)[1].split("/",1)[0]:
                raise ValueError("Wrong spreadsheet identity.")
            observed=datetime.fromisoformat(data["observed_at"].replace("Z","+00:00"))
            if observed.tzinfo is None:
                raise ValueError("Source snapshot needs UTC timezone.")
            age=self.clock()-observed.timestamp()
            if not 0<=age<=86400:
                self.observe("coo","source_health","my_tasks_connector",
                             "connector_snapshot_expired",{"age_seconds":max(0,int(age))})
                return {"status":"expired","rows":0}
            results=[]
            seen=set()
            for entry in data["rows"]:
                if not isinstance(entry,dict) or set(entry)!={
                    "row","lane","status","priority","has_external_evidence"
                }:
                    raise ValueError("Source row schema mismatch.")
                row=entry["row"]
                if type(row) is not int or not 2<=row<=1000 or row in seen:
                    raise ValueError("Invalid or duplicate canonical row.")
                seen.add(row)
                lane=entry["lane"]
                if lane not in LANES:
                    raise ValueError("Unrecognised source lane.")
                stage=entry["status"]
                if not isinstance(stage,str) or len(stage)>80 or not stage:
                    raise ValueError("Invalid source status.")
                if entry["priority"] not in ("Critical","High","Medium","Low",""):
                    raise ValueError("Invalid source priority.")
                if type(entry["has_external_evidence"]) is not bool:
                    raise ValueError("Invalid evidence marker.")
                # A human-entered sheet cell is DATA, not proof of external
                # completion. Never copy free-text, names or email addresses.
                detail={"row":row,"reported_status":stage,
                        "priority":entry["priority"],
                        "has_evidence_link":entry["has_external_evidence"],
                        "source":"existing My Tasks"}
                self.observe(lane,"canonical_task",f"my_tasks_row:{row}",
                             "reported_requires_external_readback",detail)
                results.append(row)
            self.observe("coo","source_health","my_tasks_connector",
                         "fresh_authenticated_snapshot",
                         {"source":"My Tasks","rows":len(results),"age_seconds":int(age)})
            return {"status":"fresh","rows":len(results),"age_seconds":int(age)}
        except (ValueError,TypeError,KeyError,OverflowError, OSError) as exc:
            self.observe("coo","source_health","my_tasks_connector",
                         "connector_snapshot_invalid",
                         {"reason_type":type(exc).__name__})
            return {"status":"invalid","rows":0}

    def channel_snapshots(self):
        """Existing commercial integration, read-only and independently scoped.

        ChatGPT's connected Gmail/Resend accounts do not grant the Razer their
        OAuth credentials. Missing local credential is an honest degraded
        source, never proof that mailboxes were checked.
        """
        from business_channels import (
            ChannelError, ChannelLedger, read_config as read_channel_config,
            sync as sync_channels,
        )
        from business_connector_bridge import load as connected_app_probe
        probe=connected_app_probe(self.state/"connector-capabilities.json")
        path=self.state/"channels.json"
        if path.is_symlink() or not path.is_file():
            status={"state":"not_configured","accounts":[],"coverage":False,
                    "chat_connector_probe":probe["state"]}
            self.observe("sales","source_health","business_channels","not_configured",
                         {"source":"business_channels","read_only":True})
            return status
        try:
            cfg=read_channel_config(path)
            channel=ChannelLedger(self.state/"channel-readback.sqlite")
            data=sync_channels(cfg,ledger=channel)
            source_state=("authenticated_read_only" if data["source_coverage_complete"]
                          else "local_provider_authorisation_incomplete")
            accounts=[{"alias":x["alias"],"provider":x["provider"],
                       "state":x["state"]} for x in data["accounts"]]
            self.observe("sales","source_health","business_channels",source_state,
                         {"source":"business_channels","read_only":True,
                          "accounts":accounts,
                          "source_coverage_complete":data["source_coverage_complete"]})
            return {"state":source_state,"accounts":accounts,
                    "coverage":data["source_coverage_complete"],
                    "chat_connector_probe":probe["state"],
                    "new_receipts":sum(x.get("new_messages",0) for x in data["accounts"]),
                    "suppression_holds":data["summary"]["distinct_holds"]}
        except (ChannelError,PermissionError,ValueError,sqlite3.Error,OSError) as e:
            self.observe("sales","source_health","business_channels",
                         "provider_metadata_unavailable",
                         {"source":"business_channels","reason_type":type(e).__name__})
            return {"state":"provider_metadata_unavailable","accounts":[],
                    "coverage":False,"chat_connector_probe":probe["state"]}

    def canonical_pointers(self):
        observations=[]
        for group in ("funding","partners"):
            for item in self.config[group]:
                stage=item["stage"]
                lane=item["lane"]
                self.observe(lane,group,item["id"],stage,{
                    "canonical_id":item["id"],"url":item["url"],
                    "next_gate":item["next_gate"],
                    "source_verified":False})
                observations.append({"id":item["id"],"lane":lane,
                                     "stage":stage,"next_gate":item["next_gate"]})
        for lane in LANES:
            req=MIGRATION_GATES[lane]
            self.observe("coo","migration_lane",lane,"shadow_not_cutover_ready",
                         {"lane":lane,"missing_proofs":list(req),
                          "chatgpt_task_active":True})
        return observations

    def readiness(self):
        """A self-reported JSON marker is never enough to disable an automation."""
        results=[]
        for lane in LANES:
            requirements=MIGRATION_GATES[lane]
            with self.db() as db:
                rows=db.execute(
                    "SELECT requirement,independently_verified FROM acceptance "
                    "WHERE lane=?",(lane,)
                ).fetchall()
            proofs={x["requirement"] for x in rows if x["independently_verified"]==1}
            missing=[x for x in requirements if x not in proofs]
            results.append({"lane":lane,"chatgpt_task_id":self.config["automations"][lane],
                            "phase":"parallel_shadow",
                            "cutover_allowed":False,
                            "required":list(requirements),
                            "unproven":missing,
                            "reason":"Independent provider proof and operational comparison required; automatic task shutdown prohibited."})
        return {"automations_kept_active":True,"lanes":results}

    def snapshot(self,*, limit=35):
        with self.db() as db:
            rows=db.execute("SELECT lane,kind,stage,source_ref,last_seen,changes,detail "
                            "FROM work ORDER BY last_seen DESC, lane LIMIT ?",(limit,)).fetchall()
            total=db.execute("SELECT count(*) FROM work").fetchone()[0]
            counts=db.execute(
                "SELECT lane,stage,count(*) AS n FROM work GROUP BY lane,stage"
            ).fetchall()
            last=db.execute(
                "SELECT started,ended,status,outcomes FROM runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return {"schema":SCHEMA,"at":utc(),"mode":self.config["mode"],
                "total_work_references":total,
                "counts":[{"lane":r["lane"],"stage":r["stage"],"count":r["n"]} for r in counts],
                "recent":[{"lane":r["lane"],"kind":r["kind"],"stage":r["stage"],
                           "source":r["source_ref"],"changes":r["changes"]}
                          for r in rows],
                "last_run":({"status":last["status"],
                             "summary":json.loads(last["outcomes"]),
                             "timestamp":last["ended"]}
                            if last else None),
                "migration":self.readiness()}

    def run(self, *, include_github=True):
        if self.config["mode"]!="shadow":
            raise PermissionError("External business actions disabled pending cutover proof.")
        lock=self.state/"run.lock"
        fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            try:
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                return {"status":"already_running"}
            start=self.clock()
            with self.db() as db:
                db.execute("INSERT INTO runs(started,status,outcomes) VALUES(?,?,?)",
                           (start,"running","{}"))
                run_id=db.execute("SELECT last_insert_rowid()").fetchone()[0]
            try:
                repos=self.repo_snapshots()
                issues=self.github_issue_snapshots() if include_github else []
                ci=self.ci_snapshots() if include_github else []
                leads=self.lead_snapshots()
                matters=self.canonical_pointers()
                sheet=self.sheet_snapshots()
                channels=self.channel_snapshots()
                missing_ci=sum(x.get("ci")=="unavailable" for x in ci)
                coverage={"github_ci_unavailable":missing_ci,
                          "lead_source_available":leads["status"]=="read_only",
                          "my_tasks_snapshot_fresh":sheet["status"]=="fresh",
                          "business_channel_access":channels["coverage"]}
                outcome={"repos":len(repos),"github_issues":len(issues),
                         "ci_repos_scanned":len(ci),
                         "source_coverage":coverage,
                         "lead_db":leads["status"],"leads":leads["counts"],
                         "canonical_pointers":len(matters),
                         "my_tasks_source":sheet,
                         "business_channels":channels,
                         "mode":"parallel_shadow","outbound_messages_sent":0,
                         "bids_submitted":0,"repo_writes":0,"money_moved":0}
                status=("degraded" if missing_ci or not coverage["lead_source_available"]
                        or not coverage["my_tasks_snapshot_fresh"] or
                        not coverage["business_channel_access"] else "observed")
            except Exception as exc:
                # No sensitive exception messages or raw provider output in journals.
                outcome={"error_type":type(exc).__name__,
                         "retry":"next_cycle_with_original_sources"}
                status="failed"
            with self.db() as db:
                db.execute("UPDATE runs SET ended=?,status=?,outcomes=? WHERE id=?",
                           (self.clock(),status,json.dumps(outcome),run_id))
            if status in ("observed","degraded"):
                from business_briefing import save
                save(self)
            return {"status":status,**outcome}
        finally:
            os.close(fd)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=["run","status","readiness","leads","brief"])
    p.add_argument("--without-github",action="store_true",
                   help="Run offline without requesting any GitHub issue data.")
    args=p.parse_args(argv)
    bridge=Bridge()
    if args.action=="run":
        result=bridge.run(include_github=not args.without_github)
    elif args.action=="status":
        result=bridge.snapshot()
    elif args.action=="readiness":
        result=bridge.readiness()
    elif args.action=="brief":
        from business_briefing import build,save
        result={"report":build(bridge),"saved":save(bridge)}
    else:
        result=bridge.lead_snapshots()
    print(json.dumps(result,indent=2,sort_keys=True,ensure_ascii=True))
    return 0 if result.get("status") not in ("failed",) else 2


if __name__=="__main__":
    try:
        raise SystemExit(main())
    except (ValueError,PermissionError,sqlite3.Error,OSError) as exc:
        print("Business bridge blocked: "+str(exc)[:180],file=sys.stderr)
        raise SystemExit(2)
