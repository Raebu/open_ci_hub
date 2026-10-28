#!/usr/bin/env python3
"""Official LinkedIn social posting/reactions and responses for Raeburn.

Uses LinkedIn's sanctioned HTTPS OAuth API ONLY. Can prepare, queue and
execute owner-authorised organic posts, relevant likes and comments when the
member has approved w_member_social/API access. Member following, direct
messages, connection requests and browser automation are NOT silently
replaced with unauthorised bots. Those actions can be prepared for the
account owner, not executed by this module.
"""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
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

from business_channels import _secret, fingerprint, hmac_key, ROOT_STATE
from business_outbound import encryption_key, in_uk_business_hours, OutboundRefused

DB=ROOT_STATE/"outbound.sqlite"
POLICY=ROOT_STATE/"linkedin-policy.json"
SCHEMA="raeburn.linkedin-official.v1"
URL_BASE="https://api.linkedin.com"
KINDS={"like","comment","comment_reply"}
UNSUPPORTED={"follow_member","direct_message","connection_request"}
URN_RE=re.compile(r"^urn:li:(share|activity|ugcPost):[0-9]{8,24}$")
PERSON_RE=re.compile(r"^urn:li:person:[A-Za-z0-9_-]{3,100}$")
COMMENT_RE=re.compile(r"^urn:li:comment:\(urn:li:(?:activity|share|ugcPost):[0-9]{8,24},[0-9]{8,24}\)$")
CASE=re.compile(r"^[A-Za-z0-9_.:-]{8,110}$")
LINKEDIN_VERSION="202610"
EXISTING_POSTER=Path(__file__).resolve().parent.parent / "Auto_Linkedin_Poster/raeburn-linkedin-dual-autoposter"


def approved_policy():
    return {"schema":SCHEMA,"mode":"official_api_authorised_only",
            "allowed_actions":["like","comment","comment_reply"],
            "daily_cap":8,"per_cycle_cap":2,
            "official_version":LINKEDIN_VERSION,
            "profile_follow_enabled":False,"private_messaging_enabled":False,
            "website_bot_enabled":False}


def validate_policy(value):
    if not isinstance(value,dict) or set(value)!=set(approved_policy()):
        raise OutboundRefused("Unknown LinkedIn policy structure.")
    if value!=approved_policy():
        raise OutboundRefused("Only LinkedIn official member API controls allowed.")
    return value


def provision_policy(*,confirm=False,path=POLICY):
    if not confirm:
        raise OutboundRefused("Account owner must authorise LinkedIn channel policy.")
    path=Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise OutboundRefused("Unsafe LinkedIn configuration destination.")
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if path.exists():
        existing=json.loads(path.read_text("utf8"))
        previous={**approved_policy(),
                  "allowed_actions":["publish","like","comment","comment_reply"]}
        if existing==previous:
            # Existing standalone AutoPoster is sole publisher. This is
            # an exact, backwards-compatible policy migration, not a new bot.
            import tempfile
            fd,tmp=tempfile.mkstemp(prefix=".social-policy-",dir=path.parent)
            try:
                with os.fdopen(fd,"w",encoding="utf8") as f:
                    f.write(json.dumps(approved_policy(),indent=2,sort_keys=True)+"\n")
                    f.flush()
                    os.fsync(f.fileno())
                os.chmod(tmp,0o600)
                os.replace(tmp,path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            return {"enabled":"when_official_oauth_authorised",
                    "existing":True,"migration":"publisher_consolidated"}
        if validate_policy(existing)!=approved_policy():
            raise OutboundRefused("Existing LinkedIn policy conflicts.")
        return {"enabled":"when_official_oauth_authorised","existing":True}
    data=json.dumps(approved_policy(),indent=2,sort_keys=True)+"\n"
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"w") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return {"enabled":"when_official_oauth_authorised","existing":False}


def policy_read(path=POLICY):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_mode&0o077:
        raise OutboundRefused("LinkedIn official app policy missing/unsafe.")
    return validate_policy(json.loads(path.read_text("utf8")))


def existing_publisher_token(*,root=EXISTING_POSTER,clock=time.time):
    """Reuse the owner's existing OAuth token read-only, not browser cookies.

    The existing AutoPoster owns publishing. This module may carry out
    separate relevant likes/comments only with the same official consent.
    """
    root=Path(root)
    dbpath=root/"data/autoposter.sqlite"
    envpath=root/".env"
    try:
        if (not dbpath.is_file() or dbpath.is_symlink() or
            not envpath.is_file() or envpath.is_symlink() or
            dbpath.stat().st_mode&0o077 or envpath.stat().st_mode&0o077):
            return None
        config={}
        for line in envpath.read_text(encoding="utf8").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            k,v=line.split("=",1)
            if k=="LINKEDIN_PERSON_URN":
                config[k]=v.strip().strip("'").strip('"')
        member=config.get("LINKEDIN_PERSON_URN")
        if not member or not PERSON_RE.fullmatch(member):
            return None
        with closing(sqlite3.connect(
                "file:"+quote(str(dbpath.resolve()),safe="/")+"?mode=ro",
                uri=True,timeout=4)) as db:
            row=db.execute("SELECT access_token,expires_at,scope "
                           "FROM linkedin_tokens WHERE target='personal' LIMIT 1").fetchone()
        if not row or type(row[1]) is not int or row[1]<clock()+90:
            return None
        scopes=str(row[2] or "").split()
        if not {"openid","w_member_social_feed"}.issubset(set(scopes)):
            return None
        token=row[0]
        if not isinstance(token,str) or not 16<=len(token)<=8192 or (
                "\n" in token or "\r" in token):
            return None
        return {"token":token,"member":member,"scopes":scopes,
                "source":"existing_Raeburn_LinkedIn_Dual_AutoPoster"}
    except (OSError,sqlite3.Error,UnicodeError,ValueError):
        return None


def linkedin_token(*,lookup=_secret):
    """Prefer the existing LinkedIn OAuth application, no duplicate login."""
    existing=existing_publisher_token()
    if existing:
        return existing
    text=lookup("linkedin-member-oauth","linkedin-official-oauth")
    if not text:
        return None
    try:
        data=json.loads(text)
    except (ValueError,TypeError):
        return None
    if not isinstance(data,dict) or set(data)!={"access_token","expires_at",
                                                "granted_scopes","member_urn"}:
        return None
    scopes=data["granted_scopes"]
    if not isinstance(scopes,list) or (
            not {"w_member_social_feed","openid"}.issubset(set(scopes))):
        return None
    member=data["member_urn"]
    if not isinstance(member,str) or not PERSON_RE.fullmatch(member):
        return None
    token=data["access_token"]
    if not isinstance(token,str) or not 16<=len(token)<=8192 or (
            "\n" in token or "\r" in token):
        return None
    if type(data["expires_at"]) is not int or data["expires_at"]<time.time()+90:
        return None
    return {"token":token,"member":member,"scopes":scopes}


def official_request(method,path,token,body=None,*,conn=http.client.HTTPSConnection,
                     version=LINKEDIN_VERSION):
    """Only fixed LinkedIn REST API, with correct version and no redirects."""
    if method not in ("POST","GET") or (
        not isinstance(path,str) or not path.startswith("/") or "//" in path
        or len(path)>500 or "\r" in path or "\n" in path
    ) or (not re.fullmatch(r"20[0-9]{4}",version)):
        raise OutboundRefused("Unsupported LinkedIn API request.")
    host="api.linkedin.com"
    req=conn(host,timeout=12)
    try:
        headers={"Authorization":"Bearer "+token,
                 "Linkedin-Version":version,
                 "X-Restli-Protocol-Version":"2.0.0",
                 "Accept":"application/json",
                 "User-Agent":"OpenCIHub-official-linkedin/1"}
        if method=="POST":
            headers["Content-Type"]="application/json"
        payload=json.dumps(body,sort_keys=True).encode() if body is not None else None
        req.request(method,path,body=payload,headers=headers)
        response=req.getresponse()
        status=response.status
        meta={x.lower():y for x,y in response.getheaders()}
        data=response.read(8193)
        if len(data)>8192:
            raise OutboundRefused("LinkedIn metadata response too large.")
        if method=="GET" and status!=200:
            raise OutboundRefused("LinkedIn identity readback denied.")
        if method=="POST" and status!=201:
            raise OutboundRefused("LinkedIn write declined (HTTP "+str(status)+").")
        return {"status":status,
                "restli_id":meta.get("x-restli-id") or
                meta.get("x-resourceidentity-urn"),
                "json":json.loads(data) if data.strip() else {}}
    except (OSError,TimeoutError,ValueError) as error:
        if isinstance(error,OutboundRefused):
            raise
        raise OutboundRefused("Official LinkedIn API unavailable.") from None
    finally:
        req.close()


def verify_member(token_record,*,call=official_request):
    result=call("GET","/v2/userinfo",token_record["token"])
    subject=result["json"].get("sub")
    if not isinstance(subject,str) or token_record["member"]!="urn:li:person:"+subject:
        raise OutboundRefused("LinkedIn OAuth member identity mismatch.")
    return True


def validate_action(item,*,now=None):
    now=time.time() if now is None else now
    expected={"id","kind","source_url","member_urn","target_urn","text",
              "approved_at","scheduled_at","verified_relevance",
              "verified_public_content","brand","source_case_id"}
    if not isinstance(item,dict) or set(item)!=expected:
        raise OutboundRefused("Exact LinkedIn action packet required.")
    if not isinstance(item["id"],str) or not CASE.fullmatch(item["id"]) or (
        item["kind"] not in KINDS or not isinstance(item["member_urn"],str) or
        not PERSON_RE.fullmatch(item["member_urn"])):
        raise OutboundRefused("LinkedIn approved action identity invalid.")
    if not isinstance(item["brand"],str) or item["brand"] not in (
        "Raeburn Group","Raeburn Consulting","Raeburn Ventures","TRG Recruitment","GIBP"):
        raise OutboundRefused("Unknown business brand.")
    if not isinstance(item["source_case_id"],str) or not CASE.fullmatch(
            item["source_case_id"]):
        raise OutboundRefused("No canonical business case identity.")
    url=item["source_url"]
    if not isinstance(url,str) or not (
        url.startswith("https://www.linkedin.com/feed/update/") or
        url.startswith("https://www.linkedin.com/posts/") or
        url.startswith("https://www.linkedin.com/company/") or
        url.startswith("https://www.linkedin.com/in/") or
        url.startswith("https://theraeburngroup.com/") or
        url.startswith("https://ventures.theraeburngroup.com/")):
        raise OutboundRefused("Unverified social relevance source.")
    if type(item["verified_relevance"]) is not bool or not item["verified_relevance"]:
        raise OutboundRefused("No genuine relevance evidence.")
    if type(item["verified_public_content"]) is not bool or not item[
            "verified_public_content"]:
        raise OutboundRefused("No content verification.")
    for t in ("approved_at","scheduled_at"):
        val=item[t]
        if type(val) not in (int,float) or val>now+365*86400 or val<1:
            raise OutboundRefused("Invalid schedule/authorisation timestamp.")
    if now-item["approved_at"]>7*86400:
        raise OutboundRefused("Social content approval stale.")
    if not isinstance(item["target_urn"],str) or not (
            URN_RE.fullmatch(item["target_urn"]) or (
                item["kind"]=="comment_reply" and
                COMMENT_RE.fullmatch(item["target_urn"]))):
        raise OutboundRefused("Unsupported reaction/comment target.")
    msg=item["text"]
    if item["kind"]=="like":
        if msg not in ("",None):
            raise OutboundRefused("Like has no message text.")
    elif not isinstance(msg,str) or not 20<=len(msg)<=2500 or "\x00" in msg:
        raise OutboundRefused("Professional, bounded social text required.")
    return item


def prepare_request(item):
    """Use documented OAuth REST endpoints, not website simulation."""
    action=item["kind"]
    member=item["member_urn"]
    if action=="like":
        return "/rest/reactions?actor="+quote(member,safe=""),{
            "root":item["target_urn"],"reactionType":"LIKE"}
    object_urn=item["target_urn"]
    parent=object_urn if action=="comment_reply" else None
    if parent:
        # comment_reply target can include an activity urn in parent.
        match=re.match(r"urn:li:comment:\((urn:li:(?:activity|share|ugcPost):[0-9]+),",
                       parent)
        if not match:
            raise OutboundRefused("Nested LinkedIn reply target cannot be resolved.")
        object_urn=match.group(1)
    body={"actor":member,"object":object_urn,
          "message":{"text":item["text"]}}
    if parent:
        body["parentComment"]=parent
    return "/rest/socialActions/"+quote(item["target_urn"],safe=":(),")+"/comments",body


class SocialLedger:
    def __init__(self,*,path=DB,key=None,identity_key=None,clock=time.time):
        path=Path(path)
        if path.is_symlink() or path.parent.is_symlink():
            raise OutboundRefused("Unsafe shared social action store.")
        path.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
        self.path=path
        self.key=key if key is not None else encryption_key()
        self.identity_key=identity_key if identity_key is not None else hmac_key()
        self.clock=clock
        if not isinstance(self.key,bytes) or len(self.key)!=32 or not isinstance(
                self.identity_key,bytes) or len(self.identity_key)!=32:
            raise OutboundRefused("Missing owner keyring for social execution.")
        with self.db() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS social_job(
              id TEXT PRIMARY KEY,kind TEXT NOT NULL,target_hmac TEXT,
              action_nonce BLOB NOT NULL,action_ciphertext BLOB NOT NULL,
              stage TEXT NOT NULL, due REAL NOT NULL,
              attempted_at REAL,receipt_hash TEXT,created REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_social_job ON social_job(stage,due);
            """)
        os.chmod(path,0o600)

    def db(self):
        c=sqlite3.connect(str(self.path),timeout=10,isolation_level=None)
        c.row_factory=sqlite3.Row
        c.execute("PRAGMA busy_timeout=10000")
        return closing(c)

    def enqueue(self,item,policy=None):
        validate_policy(policy if policy is not None else policy_read())
        item=validate_action(item,now=self.clock())
        key=fingerprint(self.identity_key,"social_action",item["id"]+"|"+item["kind"])
        target=fingerprint(self.identity_key,"social_target",
                           str(item["target_urn"])) if item["target_urn"] else None
        nonce=os.urandom(12)
        cipher=AESGCM(self.key).encrypt(
            nonce,json.dumps(item,sort_keys=True,separators=(",",":")).encode(),
            key.encode())
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            old=db.execute("SELECT stage FROM social_job WHERE id=?",(key,)).fetchone()
            if old:
                db.commit()
                return {"id":key,"stage":old["stage"],"duplicate":True}
            db.execute("INSERT INTO social_job VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (key,item["kind"],target,nonce,cipher,
                        "queued",item["scheduled_at"],None,None,self.clock()))
            db.commit()
        return {"id":key,"stage":"queued","duplicate":False}

    def dispatch(self,*,policy=None,token_fn=linkedin_token,
                 call=official_request,now=None):
        config=validate_policy(policy if policy is not None else policy_read())
        now=self.clock() if now is None else now
        if not in_uk_business_hours(now):
            return {"state":"outside_business_hours","attempted":0}
        record=token_fn()
        if not record:
            return {"state":"official_member_oauth_required","attempted":0}
        verify_member(record,call=call)
        with self.db() as db:
            candidates=db.execute(
                "SELECT * FROM social_job WHERE stage='queued' AND due<=? "
                "ORDER BY created LIMIT ?",(now,config["per_cycle_cap"])).fetchall()
        attempted=0
        accepted=0
        errors=[]
        for row in candidates:
            with self.db() as db:
                count=db.execute("SELECT COUNT(*) FROM social_job WHERE "
                                 "attempted_at>=?",(
                      datetime.fromtimestamp(now,ZoneInfo("Europe/London")).replace(
                        hour=0,minute=0,second=0,microsecond=0).timestamp(),)
                ).fetchone()[0]
                if count>=config["daily_cap"]:
                    errors.append("daily_social_limit")
                    break
                try:
                    item=json.loads(AESGCM(self.key).decrypt(
                        row["action_nonce"],row["action_ciphertext"],
                        row["id"].encode()))
                    validate_action(item,now=now)
                except (ValueError,TypeError,KeyError):
                    errors.append("social_action_approval_expired")
                    continue
                if item["member_urn"]!=record["member"]:
                    errors.append("linkedin_member_identity_mismatch")
                    continue
                db.execute("BEGIN IMMEDIATE")
                updated=db.execute("UPDATE social_job SET stage='uncertain',"
                                   "attempted_at=? WHERE id=? AND stage='queued'",
                                   (now,row["id"])).rowcount
                db.commit()
                if not updated:
                    continue
            attempted+=1
            try:
                path,body=prepare_request(item)
                receipt=call("POST",path,record["token"],body)
                evidence=receipt["restli_id"] or receipt["json"].get("id")
                if receipt["status"]!=201 or not isinstance(
                        evidence,str) or not evidence.startswith("urn:li:"):
                    raise OutboundRefused("Missing LinkedIn official action receipt.")
                hashed=fingerprint(self.identity_key,"linkedin_receipt",evidence)
                with self.db() as db:
                    db.execute("UPDATE social_job SET stage='accepted',"
                               "receipt_hash=? WHERE id=? AND stage='uncertain'",
                               (hashed,row["id"]))
                accepted+=1
            except Exception:
                errors.append("uncertain_action_requires_provider_readback")
        return {"state":"official_api_run","attempted":attempted,
                "accepted":accepted,"errors":sorted(set(errors)),
                "private_messages_sent":0,"follows_performed":0}

    def status(self):
        with self.db() as db:
            stats={r["stage"]:r["n"] for r in db.execute(
                "SELECT stage,COUNT(*) n FROM social_job GROUP BY stage")}
        return {"queue":stats,"supported_official_actions":sorted(KINDS),
                "unsupported_without_partner_api":sorted(UNSUPPORTED),
                "member_oauth_present":bool(linkedin_token()),
                "headless_website_interaction_enabled":False,
                "publisher":"existing_Raeburn_LinkedIn_Dual_AutoPoster"}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("activate","status","enqueue","dispatch"))
    args=parser.parse_args(argv)
    try:
        if args.action=="activate":
            result=provision_policy(confirm=True)
        elif args.action=="status":
            result=SocialLedger().status()
        elif args.action=="enqueue":
            result=SocialLedger().enqueue(json.load(sys.stdin))
        else:
            result=SocialLedger().dispatch()
        print(json.dumps(result,indent=2,sort_keys=True))
        return 0
    except (OutboundRefused,OSError,sqlite3.Error,ValueError) as exc:
        print("LinkedIn official API action blocked: "+str(exc)[:150],
              file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
