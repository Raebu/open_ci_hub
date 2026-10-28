#!/usr/bin/env python3
"""Privacy-preserving email/LinkedIn readback for Open CI Hub.

Read-only provider metadata, zero send/DM permissions. This is an execution
component of business_execution.py, NOT another CRM or mail client.
Credentials are delegated to independently authorised, owner-owned scopes.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from email.utils import parseaddr
import fcntl
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
from urllib.parse import quote, urlencode

ROOT = Path(__file__).resolve().parent
ROOT_STATE = ROOT / ".local" / "business-execution"
CONFIG = ROOT_STATE / "channels.json"
SCHEMA = "raeburn.business-channels.v1"
PROVIDERS = frozenset(("gmail","microsoft","resend","linkedin"))
SUPPORTED_READ = frozenset(("gmail","microsoft","resend"))
ALIAS = re.compile(r"^[a-z][a-z0-9-]{2,45}$")
MAIL_RE = re.compile(r"^[^\s@<>]{1,64}@[A-Za-z0-9.-]{3,190}$")
RFC_MSGID = re.compile(r"^<[^<>\r\n\x00]{1,254}>$")
ALLOWED_ORIGINS = {"gmail":"gmail.googleapis.com",
                   "microsoft":"graph.microsoft.com",
                   "resend":"api.resend.com"}
MAX_MESSAGES_PER_ACCOUNT=12
MAX_BYTES=120000
CHANNEL_DB = ROOT_STATE / "channel-readback.sqlite"


class ChannelError(ValueError):
    """Provider policy, authentication or canonical metadata check failure."""


def _is_valid_email(value):
    return isinstance(value,str) and len(value)<=254 and bool(
        MAIL_RE.fullmatch(value.strip().lower()))


def _normal_email(value):
    if not isinstance(value,str):
        return None
    addr=parseaddr(value)[1].strip().lower()
    return addr if _is_valid_email(addr) else None


def _ts(value):
    if not isinstance(value,str):
        return None
    try:
        date=datetime.fromisoformat(value.replace("Z","+00:00"))
        if date.tzinfo is None:
            return None
        result=int(date.timestamp())
        return result if 0<result<4102444800 else None
    except (ValueError,OverflowError):
        return None


def validate_config(value):
    if not isinstance(value,dict) or set(value)!={
        "schema","mode","accounts"} or value["schema"]!=SCHEMA or value["mode"]!="read_only":
        raise ChannelError("Channel config must be read-only.")
    items=value["accounts"]
    if not isinstance(items,list) or not 1<=len(items)<=20:
        raise ChannelError("Explicit, bounded channel accounts required.")
    seen=set()
    for acc in items:
        if not isinstance(acc,dict) or set(acc)!={
            "alias","provider","identity","purpose","enabled","auth_secret_label",
            "allowed_recipients"}:
            raise ChannelError("Account mapping is not exact.")
        alias=acc["alias"]
        if not isinstance(alias,str) or not ALIAS.fullmatch(alias) or alias in seen:
            raise ChannelError("Invalid or duplicate account alias.")
        seen.add(alias)
        if acc["provider"] not in PROVIDERS or type(acc["enabled"]) is not bool:
            raise ChannelError("Unrecognised provider.")
        if acc["purpose"] not in ("business_read_only","excluded_personal"):
            raise ChannelError("Personal and business account scopes must be separate.")
        if acc["purpose"]=="excluded_personal" and acc["enabled"]:
            raise ChannelError("Personal accounts cannot be included in business sync.")
        if acc["provider"]=="linkedin":
            if acc["enabled"] or acc["purpose"]!="business_read_only":
                raise ChannelError("LinkedIn messaging/browser sync is not authorised.")
            if acc["identity"]!="profile_search_connector_only":
                raise ChannelError("LinkedIn profile access is through the official connector only.")
        else:
            if acc["provider"] in ("gmail","microsoft") and not _is_valid_email(acc["identity"]):
                raise ChannelError("Expected exact authorised mailbox identity.")
            if acc["provider"]=="resend" and not (
                    isinstance(acc["identity"],str) and
                    re.fullmatch(r"[a-z0-9.-]{3,90}",acc["identity"])):
                raise ChannelError("Resend account requires bounded canonical scope.")
        label=acc["auth_secret_label"]
        if not isinstance(label,str) or (label and not ALIAS.fullmatch(label)):
            raise ChannelError("Credential selector must be a safe private label.")
        if acc["enabled"] and not label:
            raise ChannelError("Enabled native API requires its separate read-only key.")
        recipients=acc["allowed_recipients"]
        if not isinstance(recipients,list) or len(recipients)>20 or (
                len(recipients)!=len(set(recipients))) or any(
                not _is_valid_email(x) or x!=x.lower()
                for x in recipients):
            raise ChannelError("Only explicitly approved, unique receiving addresses.")
        if acc["provider"]=="resend" and acc["enabled"] and not recipients:
            raise ChannelError("Resend broad account-level mailbox access is not approved.")
    return value


def read_config(path=CONFIG):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>16384:
        raise ChannelError("No private provider configuration.")
    if os.name=="posix" and (path.stat().st_mode & 0o077):
        raise ChannelError("Provider configuration must have mode 0600.")
    return validate_config(json.loads(path.read_text("utf8")))


def fingerprint(key,category,value):
    if not isinstance(key,bytes) or len(key)<32:
        raise ChannelError("Owner-local HMAC identity key is missing.")
    if not isinstance(value,str) or not 1<=len(value)<=512:
        return None
    return hmac.new(key,(category+"|"+value.casefold().strip()).encode(),
                    hashlib.sha256).hexdigest()


def normalized_message_id(mid):
    if not isinstance(mid,str):
        return None
    mid=mid.strip()
    return mid if RFC_MSGID.fullmatch(mid) else None


def _secret(label,purpose,*,run=subprocess.run):
    if not ALIAS.fullmatch(label):
        raise ChannelError("Invalid credential selector.")
    args=["/usr/bin/secret-tool","lookup","application","open-ci-hub",
          "purpose",purpose,"account",label]
    try:
        result=run(args,capture_output=True,stdin=subprocess.DEVNULL,
                   timeout=8,check=False)
    except (OSError,subprocess.TimeoutExpired):
        return None
    if result.returncode!=0 or not result.stdout or len(result.stdout)>16000:
        return None
    try:
        return result.stdout.decode("utf8").strip()
    except UnicodeError:
        return None


def hmac_key(*,run=subprocess.run):
    v=_secret("channels-hmac","business-identity-hmac",run=run)
    if not v or not re.fullmatch(r"[a-f0-9]{64}",v):
        return None
    return bytes.fromhex(v)


def access_credential(account,*,run=subprocess.run):
    """Tokens never written to code, SQLite, logs, URL or shell arguments."""
    raw=_secret(account["auth_secret_label"],"business-provider-readonly",run=run)
    if not raw:
        return None
    try:
        data=json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data,dict) or set(data)!={"access_token","expires_at"}:
        return None
    token=data["access_token"]
    if not isinstance(token,str) or not 16<=len(token)<=8192 or (
            "\r" in token or "\n" in token):
        return None
    deadline=data["expires_at"]
    if type(deadline) is not int or deadline<time.time()+90:
        return None
    return token


def bounded_get(provider,path,token,*,connection=http.client.HTTPSConnection):
    """Static HTTPS origin, GET only, no redirect/SSRF/token-in-URL."""
    if provider not in SUPPORTED_READ or not isinstance(path,str) or (
        not path.startswith("/") or "//" in path or "\r" in path or "\n" in path or
        len(path)>1500):
        raise ChannelError("Unapproved network target.")
    host=ALLOWED_ORIGINS[provider]
    con=connection(host,timeout=9)
    try:
        auth=("Bearer "+token) if provider!="resend" else ("Bearer "+token)
        con.request("GET",path,headers={
            "Authorization":auth,"Accept":"application/json",
            "User-Agent":"OpenCIHub-read-only/1"})
        response=con.getresponse()
        status=response.status
        if status!=200:
            # Do not expose provider bodies; could include secrets or PII.
            raise ChannelError("Provider read failed (HTTP "+str(status)+").")
        raw=response.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES:
            raise ChannelError("Provider metadata exceeds bounded size.")
        parsed=json.loads(raw.decode("utf8"))
        if not isinstance(parsed,dict):
            raise ChannelError("Provider returned invalid metadata.")
        return parsed
    except (OSError,UnicodeError,ValueError,TimeoutError) as e:
        if isinstance(e,ChannelError):
            raise
        raise ChannelError("Provider connection or response unavailable.") from None
    finally:
        con.close()


def fetch_gmail(account,token,*,get=bounded_get):
    profile=get("gmail","/gmail/v1/users/me/profile",token)
    if (not isinstance(profile.get("emailAddress"),str) or
            profile["emailAddress"].casefold()!=account["identity"].casefold()):
        raise ChannelError("Gmail returned a different account identity.")
    messages=[]
    for label,direction in (("INBOX","inbound"),("SENT","outbound")):
        params=urlencode({"maxResults":6,"labelIds":label})
        page=get("gmail","/gmail/v1/users/me/messages?"+params,token)
        for message in (page.get("messages") or [])[:6]:
            mid=message.get("id") if isinstance(message,dict) else None
            if not isinstance(mid,str) or not re.fullmatch(r"[A-Za-z0-9_-]{5,180}",mid):
                continue
            q=urlencode([("format","metadata"),("metadataHeaders","Message-ID"),
                         ("metadataHeaders","In-Reply-To"),
                         ("metadataHeaders","References"),("metadataHeaders","From"),
                         ("metadataHeaders","To"),("metadataHeaders","Auto-Submitted")])
            record=get("gmail","/gmail/v1/users/me/messages/"+mid+"?"+q,token)
            headers={x.get("name","").lower():x.get("value","") for x in (
                record.get("payload",{}).get("headers") or [])
                if isinstance(x,dict)}
            messages.append({
                "provider_message_id":mid,"thread_id":record.get("threadId"),
                "message_id":headers.get("message-id"),
                "in_reply_to":headers.get("in-reply-to"),
                "sender":_normal_email(headers.get("from")),
                "recipients":[x for x in [_normal_email(headers.get("to"))] if x],
                "auto_submitted":str(headers.get("auto-submitted",""))[:60],
                "direction":direction,
                "status":"observed_in_mailbox",
                "timestamp":int(record.get("internalDate",0))/1000
                if str(record.get("internalDate","")).isdigit() else None,
            })
    return messages,[]


def fetch_microsoft(account,token,*,get=bounded_get):
    profile=get("microsoft","/v1.0/me?$select=mail,userPrincipalName",token)
    found={str(profile.get(k,"")).lower() for k in ("mail","userPrincipalName")}
    if account["identity"].lower() not in found:
        raise ChannelError("Microsoft Graph returned an unexpected account.")
    selected="id,internetMessageId,conversationId,receivedDateTime,sentDateTime,from,toRecipients,isDraft"
    messages=[]
    for folder,direction in (("inbox","inbound"),("sentitems","outbound")):
        query=urlencode({"$top":"6","$select":selected})
        items=get("microsoft",f"/v1.0/me/mailFolders/{folder}/messages?"+query,token)
        for m in (items.get("value") or [])[:6]:
            if not isinstance(m,dict) or not isinstance(m.get("id"),str):
                continue
            def addr(obj):
                return _normal_email((obj or {}).get("emailAddress",{}).get("address"))
            messages.append({
                "provider_message_id":m["id"],"thread_id":m.get("conversationId"),
                "message_id":m.get("internetMessageId"),
                "in_reply_to":None,"sender":addr(m.get("from")),
                "recipients":[v for v in (addr(x) for x in (m.get("toRecipients") or [])[:12]) if v],
                "auto_submitted":"","direction":direction,
                "status":"observed_in_mailbox",
                "timestamp":_ts(m.get("receivedDateTime") or m.get("sentDateTime")),
            })
    return messages,[]


def fetch_resend(account,token,*,get=bounded_get):
    # /emails is a documented official Resend read endpoint.
    # Do not attempt third-party inboxes on the same Resend account.
    page=get("resend","/emails?limit=12",token)
    messages=[]
    allowed=set(account["allowed_recipients"])
    for m in (page.get("data") or [])[:12]:
        if not isinstance(m,dict) or not isinstance(m.get("id"),str):
            continue
        sender=_normal_email(m.get("from"))
        if not sender or sender not in allowed:
            # This is a shared account: do not ingest unrelated senders.
            continue
        recipients=[a for a in (_normal_email(v) for v in (
            m.get("to") or [])[:15]) if a]
        messages.append({
            "provider_message_id":m["id"],
            "thread_id":None,"message_id":m.get("message_id"),
            "in_reply_to":None,"sender":sender,"recipients":recipients,
            "auto_submitted":"","direction":"outbound",
            "status":str(m.get("last_event") or m.get("status") or "sent")[:45],
            "timestamp":_ts(m.get("created_at")),
        })
    # Receiving API and account-level suppressions are checked separately.
    # A shared Resend account may include unrelated brands; ingest only
    # explicitly reviewed company inbox recipients, never unrelated mail.
    inbound=get("resend","/emails/receiving?limit=12",token)
    for m in (inbound.get("data") or [])[:12]:
        if not isinstance(m,dict) or not isinstance(m.get("id"),str):
            continue
        recipients=[x for x in (_normal_email(v) for v in (m.get("to") or [])[:20]) if x]
        if not any(x in allowed for x in recipients):
            continue
        sender=_normal_email(m.get("from"))
        messages.append({
            "provider_message_id":m["id"],"thread_id":m.get("thread_id"),
            "message_id":m.get("message_id"),"in_reply_to":m.get("in_reply_to"),
            "sender":sender,"recipients":recipients,
            "auto_submitted":"","direction":"inbound",
            "status":"received","timestamp":_ts(m.get("created_at")),
        })
    suppressed=[]
    cursor=None
    for page_no in range(5):
        target="/suppressions?limit=100"+(
            "&after="+quote(cursor,safe="") if cursor else "")
        page=get("resend",target,token)
        entries=page.get("data") or []
        if not isinstance(entries,list) or len(entries)>100:
            raise ChannelError("Suppression response invalid.")
        for item in entries:
            if not isinstance(item,dict):
                continue
            address=_normal_email(item.get("email"))
            if address:
                suppressed.append({"address":address,
                                   "origin":str(item.get("origin") or "")[:20]})
        if not page.get("has_more"):
            break
        if not entries or not isinstance(entries[-1].get("id"),str):
            raise ChannelError("Suppression pagination incomplete.")
        cursor=entries[-1]["id"]
    else:
        raise ChannelError("Suppression pagination exceeds conservative limit.")
    return messages,suppressed


def normalized_event(account,event,key):
    if not isinstance(event,dict) or not isinstance(event.get("provider_message_id"),str):
        raise ChannelError("Provider event without authoritative identity.")
    pid=event["provider_message_id"]
    if len(pid)>500 or not pid:
        raise ChannelError("Provider event identity too large.")
    identity=account["provider"]+":"+account["alias"]
    eid=fingerprint(key,"message",identity+":"+pid)
    sender=event.get("sender")
    sender_key=fingerprint(key,"contact",sender) if _is_valid_email(sender) else None
    recipients=event.get("recipients")
    if not isinstance(recipients,list) or len(recipients)>20:
        raise ChannelError("Invalid provider recipient metadata.")
    contact_keys=sorted({fingerprint(key,"contact",x) for x in recipients
                         if _is_valid_email(x)})
    if event.get("direction") not in ("inbound","outbound"):
        raise ChannelError("Unknown message direction.")
    msgid=normalized_message_id(event.get("message_id"))
    ref=normalized_message_id(event.get("in_reply_to"))
    auto=str(event.get("auto_submitted") or "").casefold()
    status=str(event.get("status") or "observed").lower()
    if len(status)>50:
        status="unknown"
    is_auto=bool(auto and auto!="no")
    # Even a verified inbound metadata receipt is NOT proof of a human reply.
    classification=("outbound" if event["direction"]=="outbound" else
                    "automatic_message" if is_auto else
                    "inbound_unclassified_hold_followups")
    ts=event.get("timestamp")
    return {
        "id":eid,"account":account["alias"],"provider":account["provider"],
        "source_id_hash":fingerprint(key,"source",pid),
        "thread_hash":fingerprint(key,"thread",identity+":"+str(event["thread_id"]))
                      if event.get("thread_id") else None,
        "rfc_message_id_hash":fingerprint(key,"rfc",msgid) if msgid else None,
        "reply_to_hash":fingerprint(key,"rfc",ref) if ref else None,
        "sender_hash":sender_key,"recipient_hashes":contact_keys,
        "direction":event["direction"],"classification":classification,
        "delivery_status":status[:50],
        "timestamp":int(ts) if type(ts) in (float,int) and 0<ts<4102444800 else None,
        "evidence":"authenticated_api_readback"
    }


class ChannelLedger:
    """Read-only provider evidence and durable do-not-send holds.

    This separate channel evidence cache is not a competing CRM. The canonical
    contact, suppression and opportunity records remain with their providers.
    """
    def __init__(self,path=CHANNEL_DB):
        path=Path(path)
        if path.is_symlink() or path.parent.is_symlink():
            raise PermissionError("Unsafe channel evidence location.")
        path.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
        self.path=path
        with self.db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS event(
                id TEXT PRIMARY KEY,account TEXT NOT NULL,provider TEXT NOT NULL,
                source_id_hash TEXT NOT NULL,thread_hash TEXT,
                rfc_message_id_hash TEXT,reply_to_hash TEXT,
                sender_hash TEXT,recipient_hashes TEXT NOT NULL,
                direction TEXT NOT NULL,classification TEXT NOT NULL,
                delivery_status TEXT NOT NULL,timestamp INTEGER,
                evidence TEXT NOT NULL,first_seen REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS hold(
                identity_hash TEXT PRIMARY KEY,reason TEXT NOT NULL,
                first_seen REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS sync(
                account TEXT PRIMARY KEY,provider TEXT NOT NULL,
                state TEXT NOT NULL,seen INTEGER NOT NULL,last_checked REAL NOT NULL,
                explanation TEXT NOT NULL);
            """)
        os.chmod(path,0o600)

    def db(self):
        con=sqlite3.connect(str(self.path),timeout=12,isolation_level=None)
        con.row_factory=sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000")
        return closing(con)

    def add(self,items):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            fresh=0
            for x in items:
                exists=db.execute("SELECT id FROM event WHERE id=?",(x["id"],)).fetchone()
                if exists:
                    continue
                db.execute("INSERT INTO event VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (x["id"],x["account"],x["provider"],x["source_id_hash"],
                     x["thread_hash"],x["rfc_message_id_hash"],x["reply_to_hash"],
                     x["sender_hash"],json.dumps(x["recipient_hashes"]),
                     x["direction"],x["classification"],x["delivery_status"],
                     x["timestamp"],x["evidence"],time.time()))
                fresh+=1
                if x["classification"]=="inbound_unclassified_hold_followups" and x["sender_hash"]:
                    db.execute("INSERT OR IGNORE INTO hold VALUES(?,?,?)",
                               (x["sender_hash"],"inbound_requires_reply_review",time.time()))
                if x["delivery_status"] in ("bounced","bounce","complained","complaint","suppressed"):
                    for contact in x["recipient_hashes"]:
                        db.execute("INSERT OR IGNORE INTO hold VALUES(?,?,?)",
                                   (contact,"provider_delivery_or_suppression",time.time()))
            db.commit()
        return fresh

    def add_suppressions(self,items,key):
        if not isinstance(items,list) or len(items)>500:
            raise ChannelError("Unbounded provider suppression data.")
        fresh=0
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            for entry in items:
                if not isinstance(entry,dict) or set(entry)!={"address","origin"}:
                    raise ChannelError("Unrecognised suppression metadata.")
                address=_normal_email(entry["address"])
                if not address or entry["origin"] not in (
                        "bounce","complaint","manual"):
                    raise ChannelError("Untrusted suppression category.")
                identity=fingerprint(key,"contact",address)
                cur=db.execute("INSERT OR IGNORE INTO hold VALUES(?,?,?)",
                               (identity,"provider_suppression",time.time()))
                fresh+=cur.rowcount
            db.commit()
        return fresh

    def record_sync(self,account,state,seen,reason):
        if not ALIAS.fullmatch(account["alias"]) or len(reason)>80:
            raise ChannelError("Invalid status.")
        with self.db() as db:
            db.execute("INSERT INTO sync VALUES(?,?,?,?,?,?) ON CONFLICT(account)"
                       " DO UPDATE SET state=excluded.state,seen=excluded.seen,"
                       " last_checked=excluded.last_checked,explanation=excluded.explanation",
                       (account["alias"],account["provider"],state,seen,time.time(),reason))

    def report(self):
        with self.db() as db:
            statuses=[dict(r) for r in db.execute(
                "SELECT account,provider,state,seen,last_checked,explanation "
                "FROM sync ORDER BY account")]
            stats={r["classification"]:r["n"] for r in db.execute(
                "SELECT classification,COUNT(*) AS n FROM event GROUP BY classification")}
            held=db.execute("SELECT count(*) FROM hold").fetchone()[0]
            correlated=db.execute("SELECT count(*) FROM "
                                  "(SELECT rfc_message_id_hash FROM event "
                                  "WHERE rfc_message_id_hash IS NOT NULL "
                                  "GROUP BY rfc_message_id_hash HAVING COUNT(*)>1)").fetchone()[0]
        return {"provider_status":statuses,"ingested_message_counts":stats,
                "distinct_holds":held,
                "cross_provider_rfc_correlations":correlated,
                "sending_enabled":False,"linkedin_messaging_enabled":False,
                "business_contacts_exported":0}

    def eligible_to_contact(self,email,key):
        # This bridge is only a read-only readback; NO email is send-ready
        # regardless of whether a source has a known suppression record.
        value=fingerprint(key,"contact",_normal_email(email)) if _normal_email(email) else None
        with self.db() as db:
            held=bool(value and db.execute(
                "SELECT 1 FROM hold WHERE identity_hash=?",(value,)).fetchone())
        return {"can_send":False,"held":held,
                "reason":"read_only_migration_and_canonical_suppression_check_required"}


def sync(config=None,*, ledger=None,key=None,secret=access_credential,get=bounded_get):
    cfg=validate_config(config) if config is not None else read_config()
    ledger=ledger or ChannelLedger()
    key=key if key is not None else hmac_key()
    results=[]
    sync_started=time.monotonic()
    def budgeted_get(provider,path,token):
        if time.monotonic()-sync_started>45:
            raise ChannelError("Read-only provider budget exceeded.")
        return get(provider,path,token)
    for account in cfg["accounts"]:
        status={"alias":account["alias"],"provider":account["provider"],
                "state":"not_activated","new_messages":0}
        if account["purpose"]=="excluded_personal":
            status["state"]="excluded_personal"
        elif account["provider"]=="linkedin":
            status["state"]="connector_profile_lookup_only"
        elif not account["enabled"]:
            status["state"]="disabled_by_policy"
        elif key is None:
            status["state"]="needs_private_hmac_key"
        else:
            credential=secret(account)
            if not credential:
                status["state"]="needs_independent_provider_oauth_or_api_key"
            else:
                try:
                    collector={"gmail":fetch_gmail,"microsoft":fetch_microsoft,
                               "resend":fetch_resend}[account["provider"]]
                    events,suppressions=collector(account,credential,get=budgeted_get)
                    valid=[normalized_event(account,v,key) for v in events]
                    status["new_messages"]=ledger.add(valid)
                    status["new_suppression_holds"]=ledger.add_suppressions(
                        suppressions,key)
                    status["state"]="authenticated_read_only"
                except (ChannelError,KeyError,TypeError) as exc:
                    status["state"]="provider_read_unavailable"
        ledger.record_sync(account,status["state"],status["new_messages"],
                           "No write or send permission")
        results.append(status)
    report=ledger.report()
    return {"accounts":results,"summary":report,
            "source_coverage_complete":all(
                result["state"]=="authenticated_read_only"
                for account,result in zip(cfg["accounts"],results)
                if account["provider"] in SUPPORTED_READ and
                account["purpose"]=="business_read_only" and account["enabled"]),
            "mail_sent":0,"linkedin_messages_sent":0}


def cli(argv=None):
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=("status","sync","accounts"))
    opts=p.parse_args(argv)
    if opts.action=="accounts":
        cfg=read_config()
        output={"schema":SCHEMA,"accounts":[{
            "alias":x["alias"],"provider":x["provider"],
            "purpose":x["purpose"],"enabled":x["enabled"],
            "credentials_stored_in_git":False} for x in cfg["accounts"]]}
    elif opts.action=="status":
        output=ChannelLedger().report()
    else:
        output=sync()
    print(json.dumps(output,indent=2,sort_keys=True))
    return 0


if __name__=="__main__":
    try:
        raise SystemExit(cli())
    except (ChannelError,PermissionError,sqlite3.Error,OSError) as e:
        print("Channel readback refused: "+str(e)[:170])
        raise SystemExit(2)
