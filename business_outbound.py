#!/usr/bin/env python3
"""Governed business outbound execution on existing Open CI Hub timer.

Owner-authorised sending is enabled at POLICY level. Actual sends require an
independent scoped provider token, a verified canonical handoff, fresh unified
mailbox/suppression readback, UK working hours and an exact one-to-one task.
No browser/LinkedIn private messaging, mass blast, guessing or blind retries.
Every request is reserved durably BEFORE network I/O; uncertain outcomes
require provider reconciliation. Message bodies and recipients are encrypted.
"""
from __future__ import annotations
import argparse
import base64
from contextlib import closing
from datetime import datetime, timezone
from email.message import EmailMessage
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.parse import quote
from zoneinfo import ZoneInfo
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from business_channels import (
    ROOT_STATE, ChannelError, ALIAS, _is_valid_email, _normal_email, _secret,
    fingerprint, hmac_key, ChannelLedger,
)

POLICY = ROOT_STATE/"outbound-policy.json"
OUTBOX = ROOT_STATE/"outbound.sqlite"
SCHEMA = "raeburn.governed-outbound.v1"
MAILBOXES = {"raeburn-primary":("gmail","martin@theraeburngroup.com"),
             "raeburn-contact":("gmail","contact@theraeburngroup.com"),
             "raeburn-resend":("resend","support@theraeburngroup.com")}
CASE_RE = re.compile(r"^[A-Za-z0-9_.:-]{6,110}$")
MESSAGE_ID_RE = re.compile(r"^<[!-~]{4,250}>$")
MAX_DAILY=35
MAX_TOTAL=500
SOURCE_STATES = ("authenticated_read_only",)
FRESH_FOR = 900  # no stale or ChatGPT-only suppression/readback


class OutboundRefused(ValueError):
    pass


def safe_config(value):
    if not isinstance(value,dict) or set(value)!={
        "schema","mode","sender_permissions","max_daily","max_per_run",
        "min_seconds_between","approved_case_lanes","allow_approved_followups",
        "chatgpt_parallel_migration"}:
        raise OutboundRefused("Unexpected outbound policy fields.")
    if value["schema"]!=SCHEMA or value["mode"] not in (
            "autonomous_qualified","pause"):
        raise OutboundRefused("Unknown outbound execution policy.")
    p=value["sender_permissions"]
    if not isinstance(p,dict) or set(p)!=set(MAILBOXES):
        raise OutboundRefused("Every business sender requires an exact rule.")
    allowed={"initial","followup","reply","service"}
    for alias,kinds in p.items():
        if not isinstance(kinds,list) or len(kinds)!=len(set(kinds)) or (
                not set(kinds).issubset(allowed)):
            raise OutboundRefused("Unknown sender type permission.")
        if alias!="raeburn-primary" and any(
                x in ("initial","followup") for x in kinds):
            raise OutboundRefused("Only approved named business sender for prospecting.")
    if (type(value["max_daily"]) is not int or not 1<=value["max_daily"]<=MAX_DAILY or
        type(value["max_per_run"]) is not int or not 1<=value["max_per_run"]<=4 or
        type(value["min_seconds_between"]) is not int or
        not 120<=value["min_seconds_between"]<=86400 or
        value["max_per_run"]>value["max_daily"]):
        raise OutboundRefused("Unapproved sending volume.")
    if value["approved_case_lanes"]!=["sales","portfolio"] or type(
            value["allow_approved_followups"]) is not bool or type(
            value["chatgpt_parallel_migration"]) is not bool or (
            not value["chatgpt_parallel_migration"]):
        raise OutboundRefused("Canonical sales migration safety required.")
    return value


def read_policy(path=POLICY):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>10000:
        raise OutboundRefused("Outbound policy not provisioned.")
    if os.name=="posix" and path.stat().st_mode&0o077:
        raise OutboundRefused("Private policy file permissions invalid.")
    return safe_config(json.loads(path.read_text(encoding="utf8")))


def owner_policy():
    """Explicitly requested autonomous business communications, not a send."""
    return safe_config({
        "schema":SCHEMA,"mode":"autonomous_qualified",
        "sender_permissions":{
            "raeburn-primary":["initial","followup","reply"],
            "raeburn-contact":["reply","service"],
            "raeburn-resend":["reply","service"],
        },
        "max_daily":25,"max_per_run":2,"min_seconds_between":180,
        "approved_case_lanes":["sales","portfolio"],
        "allow_approved_followups":True,
        "chatgpt_parallel_migration":True,
    })


def install_policy(*,approve=False,path=POLICY):
    if approve is not True:
        raise OutboundRefused("Owner approval required.")
    path=Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise OutboundRefused("Unsafe policy location.")
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    value=owner_policy()
    if path.exists():
        if read_policy(path)!=value:
            raise OutboundRefused("Existing outbound policy differs; preserve it.")
        return {"policy":"already_configured","mode":value["mode"]}
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,"w",encoding="utf8") as f:
            f.write(json.dumps(value,indent=2,sort_keys=True)+"\n")
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return {"policy":"configured","mode":value["mode"],
            "max_daily":value["max_daily"],
            "per_run":value["max_per_run"],
            "initial_send_available_when_gated":True}


def encryption_key(*,run=None):
    """Provision independently; never reuse contact HMAC as AES key."""
    raw=_secret("outbound-encryption","business-outbound-aes")
    if not raw or not re.fullmatch(r"[a-f0-9]{64}",raw):
        return None
    return bytes.fromhex(raw)


def init_encryption_key(*,run=None,lookup=_secret):
    import secrets,subprocess
    existing=lookup("outbound-encryption","business-outbound-aes")
    if existing:
        if not re.fullmatch(r"[a-f0-9]{64}",existing):
            raise OutboundRefused("Existing encryption key malformed, refused rotation.")
        return {"key":"already_exists"}
    runner=run or subprocess.run
    value=secrets.token_hex(32)
    args=["/usr/bin/secret-tool","store",
          "--label=Open CI Hub encrypted business message outbox",
          "application","open-ci-hub","purpose","business-outbound-aes",
          "account","outbound-encryption"]
    try:
        response=runner(args,input=(value+"\n").encode(),capture_output=True,
                        timeout=12,check=False)
    except (OSError,subprocess.TimeoutExpired):
        raise OutboundRefused("Protected keyring unavailable.") from None
    if response.returncode or lookup("outbound-encryption","business-outbound-aes")!=value:
        raise OutboundRefused("Protected encryption key was not stored successfully.")
    return {"key":"created_protected_key"}


def provider_credential(alias,*,secret=_secret):
    if alias not in MAILBOXES:
        raise OutboundRefused("Unrecognised sender identity.")
    raw=secret("outbound-"+alias,"business-provider-outbound")
    if not raw:
        return None
    try:
        data=json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data,dict) or set(data)!={"token","expires_at",
                                               "provider","sender"}:
        return None
    if data["provider"]!=MAILBOXES[alias][0] or data["sender"]!=MAILBOXES[alias][1]:
        return None
    if not isinstance(data["token"],str) or not 16<=len(data["token"])<=8192 or (
            "\n" in data["token"] or "\r" in data["token"]):
        return None
    if type(data["expires_at"]) is not int or data["expires_at"]<time.time()+90:
        return None
    return data["token"]


def validate_draft(draft,*,now=None):
    now=time.time() if now is None else now
    expected={"case_id","lane","kind","sender","to","subject","body",
              "company_domain","subscriber_category","lawful_basis",
              "source_url","canonical_owner","handoff_verified_at",
              "last_inbox_readback_at","last_suppression_readback_at",
              "explicit_opt_out","prior_attempts","initial_sent_at",
              "last_human_reply_at","due_at","in_reply_to","thread_id"}
    if not isinstance(draft,dict) or set(draft)!=expected:
        raise OutboundRefused("Exact source-backed one-recipient draft required.")
    if not isinstance(draft["case_id"],str) or not CASE_RE.fullmatch(draft["case_id"]):
        raise OutboundRefused("Canonical case reference invalid.")
    if draft["lane"] not in ("sales","portfolio") or draft["kind"] not in (
            "initial","followup","reply","service"):
        raise OutboundRefused("Unapproved business correspondence class.")
    if draft["sender"] not in MAILBOXES or not _is_valid_email(draft["to"]):
        raise OutboundRefused("Recipient or approved sending identity invalid.")
    if draft["to"].lower()!=draft["to"] or not isinstance(
            draft["company_domain"],str) or not re.fullmatch(
            r"[a-z0-9.-]{4,160}",draft["company_domain"]):
        raise OutboundRefused("Company identity not verified.")
    if draft["to"].rsplit("@",1)[-1]!=draft["company_domain"]:
        raise OutboundRefused("Recipient not associated with verified company domain.")
    if draft["subscriber_category"] not in ("uk_corporate","opted_in") or (
        draft["lawful_basis"] not in ("legitimate_interests","consent","existing_customer")):
        raise OutboundRefused("Lawful business outreach category not established.")
    if draft["subscriber_category"]=="opted_in" and draft["lawful_basis"]!="consent":
        raise OutboundRefused("Individual/unknown subscriber requires consent evidence.")
    if (type(draft["explicit_opt_out"]) is not bool or draft["explicit_opt_out"] or
        draft["canonical_owner"]!="open_ci_hub"):
        raise OutboundRefused("Contact suppressed or owned by existing ChatGPT engine.")
    if not isinstance(draft["source_url"],str) or not draft["source_url"].startswith(
            "https://") or len(draft["source_url"])>450:
        raise OutboundRefused("Source provenance missing.")
    if (not isinstance(draft["subject"],str) or
        not 1<=len(draft["subject"])<=160 or any(x in draft["subject"]
                                                  for x in ("\r","\n","\x00")) or
        not isinstance(draft["body"],str) or not 40<=len(draft["body"])<=5000 or
        "\x00" in draft["body"]):
        raise OutboundRefused("Bounded, complete message required.")
    if not re.search(r"opt.?out|unsubscribe|do not contact|don't contact",
                     draft["body"],re.I) and draft["kind"] in ("initial","followup"):
        raise OutboundRefused("Unsubscribe or opt-out instructions missing.")
    if type(draft["prior_attempts"]) is not int or not 0<=draft["prior_attempts"]<=2:
        raise OutboundRefused("Attempt count unavailable.")
    for k in ("handoff_verified_at","last_inbox_readback_at",
              "last_suppression_readback_at","due_at"):
        val=draft[k]
        if type(val) not in (int,float) or not 0<val<=now+365*86400:
            raise OutboundRefused("Invalid verified scheduling timestamp.")
        if k!="due_at" and not 0<=now-val<=FRESH_FOR:
            raise OutboundRefused("Required recent canonical/mailbox/suppression evidence stale.")
    if draft["initial_sent_at"] is not None and type(draft["initial_sent_at"]) not in (
            int,float):
        raise OutboundRefused("Invalid initial sent timestamp.")
    if draft["last_human_reply_at"] is not None and type(draft["last_human_reply_at"]) not in (
            int,float):
        raise OutboundRefused("Invalid reply state.")
    if draft["kind"]=="initial" and (draft["prior_attempts"]!=0 or
                                    draft["initial_sent_at"] is not None):
        raise OutboundRefused("Not a net-new contact.")
    if draft["kind"]=="followup":
        offset={1:3,2:7}
        if (draft["prior_attempts"] not in offset or
            draft["initial_sent_at"] is None or
            draft["last_human_reply_at"] is not None or
            draft["due_at"]<draft["initial_sent_at"]+offset[draft["prior_attempts"]]*86400):
            raise OutboundRefused("Follow-up violates Day 3/7 or reply-stop policy.")
    if draft["kind"]=="reply":
        if draft["last_human_reply_at"] is None or not draft["in_reply_to"]:
            raise OutboundRefused("Replies require verified existing conversation.")
    if draft["in_reply_to"] is not None and (
            not isinstance(draft["in_reply_to"],str) or not
            MESSAGE_ID_RE.fullmatch(draft["in_reply_to"])):
        raise OutboundRefused("Invalid threading header.")
    if draft["thread_id"] is not None and not isinstance(draft["thread_id"],str):
        raise OutboundRefused("Invalid provider thread.")
    return draft


def in_uk_business_hours(now=None):
    date=datetime.fromtimestamp(time.time() if now is None else now,ZoneInfo("Europe/London"))
    return date.weekday()<5 and 9<=date.hour<17


def outbound_post(alias,token,payload,job_key,*,conn=http.client.HTTPSConnection):
    """One fixed endpoint per provider; do not retry ambiguous posts."""
    provider,sender=MAILBOXES[alias]
    if provider=="gmail":
        msg=EmailMessage()
        msg["From"]=sender
        msg["To"]=payload["to"]
        msg["Subject"]=payload["subject"]
        if payload.get("in_reply_to"):
            msg["In-Reply-To"]=payload["in_reply_to"]
            msg["References"]=payload["in_reply_to"]
        msg.set_content(payload["body"])
        raw=base64.urlsafe_b64encode(msg.as_bytes()).decode()
        body={"raw":raw}
        if payload.get("thread_id"):
            body["threadId"]=payload["thread_id"]
        host,path="gmail.googleapis.com","/gmail/v1/users/me/messages/send"
        ok=200
    else:
        body={"from":sender,"to":[payload["to"]],"subject":payload["subject"],
              "text":payload["body"],"reply_to":sender}
        if payload.get("in_reply_to"):
            body["headers"]={"In-Reply-To":payload["in_reply_to"],
                             "References":payload["in_reply_to"]}
        host,path="api.resend.com","/emails"
        ok=200
    headers={"Authorization":"Bearer "+token,"Content-Type":"application/json",
             "Accept":"application/json","User-Agent":"OpenCIHub-governed-outbound/1"}
    if provider=="resend":
        headers["Idempotency-Key"]=job_key
    connection=conn(host,timeout=12)
    try:
        connection.request("POST",path,body=json.dumps(body).encode(),headers=headers)
        response=connection.getresponse()
        status=response.status
        result=response.read(4097)
        if len(result)>4096:
            raise OutboundRefused("Provider response exceeded receipt limit.")
        if status!=ok:
            raise OutboundRefused("Provider send response HTTP "+str(status))
        data=json.loads(result.decode())
        message_id=data.get("id")
        if not isinstance(message_id,str) or not 4<=len(message_id)<=200:
            raise OutboundRefused("Missing authoritative provider send receipt.")
        return {"provider":provider,"message_id":message_id,
                "thread_id":data.get("threadId") if provider=="gmail" else None}
    finally:
        connection.close()


class Outbox:
    def __init__(self,path=OUTBOX,*,key=None,identity_key=None,clock=time.time):
        path=Path(path)
        if path.is_symlink() or path.parent.is_symlink():
            raise PermissionError("Unsafe outbox path.")
        path.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
        self.path=path
        self.clock=clock
        self.key=key if key is not None else encryption_key()
        self.identity_key=identity_key if identity_key is not None else hmac_key()
        if not isinstance(self.key,bytes) or len(self.key)!=32 or not isinstance(
                self.identity_key,bytes) or len(self.identity_key)!=32:
            raise OutboundRefused("Both protected identity and encryption keys required.")
        with self.db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS job(
              id TEXT PRIMARY KEY, sender TEXT NOT NULL, kind TEXT NOT NULL,
              lane TEXT NOT NULL, contact_hmac TEXT NOT NULL,
              company_hmac TEXT NOT NULL, due REAL NOT NULL,
              nonce BLOB NOT NULL, ciphertext BLOB NOT NULL,
              state TEXT NOT NULL, attempted_at REAL,
              provider_receipt_hmac TEXT, created REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_job_state_due ON job(state,due);
            CREATE TABLE IF NOT EXISTS log(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              job_id TEXT NOT NULL, state TEXT NOT NULL, at REAL NOT NULL
            );
            """)
        os.chmod(path,0o600)

    def db(self):
        con=sqlite3.connect(str(self.path),timeout=10,isolation_level=None)
        con.row_factory=sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000")
        return closing(con)

    def enqueue(self,draft,policy=None):
        policy=safe_config(policy) if policy is not None else read_policy()
        draft=validate_draft(draft,now=self.clock())
        if policy["mode"]!="autonomous_qualified" or draft["kind"] not in policy[
                "sender_permissions"][draft["sender"]]:
            raise OutboundRefused("Owner did not enable this outbound action.")
        ident=fingerprint(self.identity_key,"case_attempt",
            draft["case_id"]+"|"+draft["sender"]+"|"+str(draft["prior_attempts"]))
        contact=fingerprint(self.identity_key,"contact",draft["to"])
        company=fingerprint(self.identity_key,"company",draft["company_domain"])
        nonce=os.urandom(12)
        ciphertext=AESGCM(self.key).encrypt(
            nonce,json.dumps(draft,separators=(",",":"),sort_keys=True).encode(),
            ident.encode())
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            old=db.execute("SELECT id,state FROM job WHERE id=?",(ident,)).fetchone()
            if old:
                db.commit()
                return {"id":ident,"state":old["state"],"duplicate":True}
            db.execute("INSERT INTO job VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ident,draft["sender"],draft["kind"],draft["lane"],contact,
                 company,draft["due_at"],nonce,ciphertext,"queued",None,None,
                 self.clock()))
            db.execute("INSERT INTO log(job_id,state,at) VALUES(?,?,?)",
                       (ident,"queued",self.clock()))
            db.commit()
        return {"id":ident,"state":"queued","duplicate":False}

    def report(self):
        with self.db() as db:
            counts={r["state"]:r["n"] for r in db.execute(
                "SELECT state,COUNT(*) n FROM job GROUP BY state")}
            last=[{"id":r["id"],"state":r["state"],"sender":r["sender"],
                   "kind":r["kind"],"lane":r["lane"]}
                  for r in db.execute(
                "SELECT id,state,sender,kind,lane FROM job ORDER BY created DESC LIMIT 12")]
        return {"queue_counts":counts,"recent":last,
                "policy_mode":(read_policy()["mode"] if POLICY.exists() else
                               "not_provisioned"),
                "sending_path_enabled_by_owner":POLICY.exists(),
                "provider_creds_verified":False,
                "no_blind_retries":True}

    def _local_reconcile(self,alias,contact,company,now,kind):
        """Require all company inboxes and global provider suppressions fresh.

        This gate is stronger than a ChatGPT capability probe; the independent
        provider daemon must actually have read permissions to qualify sends.
        """
        with ChannelLedger(ROOT_STATE/"channel-readback.sqlite").db() as db:
            checks=db.execute(
                "SELECT account,state,last_checked FROM sync WHERE account IN "
                "('raeburn-primary','raeburn-contact','raeburn-resend')").fetchall()
            values={r["account"]:r for r in checks}
            if set(values)!={"raeburn-primary","raeburn-contact","raeburn-resend"} or any(
                row["state"] not in SOURCE_STATES or now-row["last_checked"]>FRESH_FOR
                or row["last_checked"]>now+10 for row in values.values()):
                return "all_provider_readback_required"
            for key in (contact,company):
                rows=db.execute("SELECT reason FROM hold WHERE identity_hash=?",(
                    key,)).fetchall()
                for evidence in rows:
                    if (kind=="reply" and key==contact and
                        evidence["reason"]=="inbound_requires_reply_review"):
                        continue
                    return "contact_or_company_suppressed"
        return None

    def dispatch(self,*,policy=None,credential=provider_credential,
                 sender=outbound_post,now=None):
        policy=safe_config(policy) if policy is not None else read_policy()
        now=self.clock() if now is None else now
        if policy["mode"]!="autonomous_qualified":
            return {"status":"paused","attempted":0,"sent":0}
        if not in_uk_business_hours(now):
            return {"status":"outside_business_hours","attempted":0,"sent":0}
        sent=0; attempted=0; blocked=[]
        with self.db() as db:
            rows=db.execute("SELECT * FROM job WHERE state='queued' AND due<=? "
                            "ORDER BY created LIMIT ?",(now,policy["max_per_run"])).fetchall()
        for row in rows:
            reject=self._local_reconcile(row["sender"],row["contact_hmac"],
                                         row["company_hmac"],now,row["kind"])
            if reject:
                blocked.append(reject)
                continue
            token=credential(row["sender"])
            if not token:
                blocked.append("sender_oauth_or_api_key_missing")
                continue
            with self.db() as db:
                today=datetime.fromtimestamp(now,ZoneInfo("Europe/London")).date()
                start=datetime(today.year,today.month,today.day,
                               tzinfo=ZoneInfo("Europe/London")).timestamp()
                n=db.execute("SELECT COUNT(*) FROM job WHERE attempted_at>=?",
                             (start,)).fetchone()[0]
                recent=db.execute("SELECT MAX(attempted_at) FROM job").fetchone()[0]
                if n>=policy["max_daily"]:
                    blocked.append("daily_budget_exhausted")
                    continue
                if recent is not None and now-recent<policy["min_seconds_between"]:
                    blocked.append("send_spacing_not_met")
                    continue
                try:
                    payload=json.loads(AESGCM(self.key).decrypt(
                        row["nonce"],row["ciphertext"],row["id"].encode()))
                    validate_draft(payload,now=now)
                except (ValueError,KeyError,TypeError):
                    blocked.append("draft_proof_stale_or_tampered")
                    continue
                # Queue may have originated in a different automation.
                if payload["sender"]!=row["sender"]:
                    blocked.append("sender_identity_mismatch")
                    continue
                db.execute("BEGIN IMMEDIATE")
                changed=db.execute(
                    "UPDATE job SET state='dispatch_uncertain',attempted_at=? "
                    "WHERE id=? AND state='queued'",(now,row["id"])).rowcount
                if changed:
                    db.execute("INSERT INTO log(job_id,state,at) VALUES(?,?,?)",
                               (row["id"],"dispatch_uncertain",now))
                db.commit()
                if not changed:
                    continue
            attempted+=1
            try:
                receipt=sender(row["sender"],token,payload,row["id"])
                if (not isinstance(receipt,dict) or receipt.get("provider")!=
                        MAILBOXES[row["sender"]][0] or not isinstance(
                        receipt.get("message_id"),str)):
                    raise OutboundRefused("Provider receipt mismatch.")
                hashed=fingerprint(self.identity_key,"provider_receipt",
                                   receipt["provider"]+":"+receipt["message_id"])
                with self.db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("UPDATE job SET state='accepted_pending_delivery',"
                               "provider_receipt_hmac=? WHERE id=? AND "
                               "state='dispatch_uncertain'",(hashed,row["id"]))
                    db.execute("INSERT INTO log(job_id,state,at) VALUES(?,?,?)",
                               (row["id"],"accepted_pending_delivery",now))
                    db.commit()
                sent+=1
            except Exception:
                # Already reserved. Even Resend idempotency expires after 24h.
                # Never resend uncertain attempts without exact provider lookup.
                blocked.append("uncertain_provider_result_manual_reconcile")
        return {"status":"scanned","attempted":attempted,
                "accepted_pending_delivery":sent,"blocked":sorted(set(blocked)),
                "confirmed_delivered":0,"linkedin_dms_sent":0}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="action",required=True)
    sub.add_parser("activate")
    sub.add_parser("status")
    sub.add_parser("dispatch")
    sub.add_parser("enqueue")
    args=p.parse_args(argv)
    try:
        if args.action=="activate":
            status=install_policy(approve=True)
            status["encryption"]=init_encryption_key()
        elif args.action=="status":
            status=Outbox().report()
        elif args.action=="enqueue":
            draft=json.load(sys.stdin)
            status=Outbox().enqueue(draft)
        else:
            status=Outbox().dispatch()
        print(json.dumps(status,sort_keys=True,indent=2))
        return 0
    except (OutboundRefused,PermissionError,ChannelError,OSError,
            ValueError,sqlite3.Error) as exc:
        print("Governed outbound blocked: "+str(exc)[:180],file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
