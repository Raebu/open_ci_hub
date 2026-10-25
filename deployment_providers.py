"""Credential-isolated deployment adapters for approved immutable provider revisions.

No secrets are accepted in repository manifests, CLI argv or result dictionaries.
Providers only promote already built/verified immutable versions. No general shell.
"""
from __future__ import annotations
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
import uuid
import ssl

IDENT = re.compile(r"^[a-z][a-z0-9._-]{1,80}$")
HOST = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.-]{1,250}$")
UUID = re.compile(r"^[0-9a-fA-F-]{36}$")
DEPLOY_ID = re.compile(r"^(?:dpl_)?[a-zA-Z0-9_-]{10,90}$")
IMAGE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/@-]{4,240}@sha256:[0-9a-f]{64}$")
WORKER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
PATH = re.compile(r"^/[a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)*$")

class CredentialStore:
    def __init__(self, root: Path):
        self.root = Path(root)
    def file(self, name):
        if not isinstance(name,str) or not IDENT.fullmatch(name):
            raise ValueError("Unrecognised credential name")
        if self.root.is_symlink() or not self.root.is_dir():
            raise PermissionError("Private credential store is not configured")
        st = self.root.stat()
        if st.st_mode & 0o077 or st.st_uid != os.getuid():
            raise PermissionError("Credential store ownership/permissions invalid")
        path = self.root / name
        if path.is_symlink() or not path.is_file():
            raise PermissionError("Credential not installed")
        st = path.stat()
        if st.st_mode & 0o077 or st.st_uid != os.getuid() or st.st_size > 8192:
            raise PermissionError("Credential ownership/permissions invalid")
        return path
    def token(self,name):
        token=self.file(name).read_text(encoding="utf-8").strip()
        if not token or len(token)>4096 or any(ord(c)<33 for c in token):
            raise RuntimeError("Credential format invalid")
        return token


def https_json(method, url, *, token, payload=None, timeout=15):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!="https" or parsed.username or parsed.password or not parsed.hostname:
        raise ValueError("HTTPS provider API required")
    if parsed.hostname not in ("api.cloudflare.com","api.vercel.com"):
        raise ValueError("Only known provider API hosts permitted")
    if method not in ("GET","POST"):
        raise ValueError("Unsupported provider operation")
    data=None if payload is None else json.dumps(payload,sort_keys=True).encode()
    req=urllib.request.Request(url,data=data,method=method,headers={
        "Authorization":"Bearer "+token,"Content-Type":"application/json",
        "Accept":"application/json","User-Agent":"Open-CI-Hub-Deploy/1"
    })
    try:
        with urllib.request.urlopen(req,timeout=timeout,context=ssl.create_default_context()) as resp:
            raw=resp.read(500_001)
            if len(raw)>500_000:
                raise RuntimeError("Provider response too large")
    except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError) as exc:
        # Do not log full URLs or remote message bodies; they can contain secrets.
        raise RuntimeError("Provider request failed ("+str(getattr(exc,"code","connection"))+")") from None
    try:
        value=json.loads(raw)
    except ValueError:
        raise RuntimeError("Provider returned malformed JSON") from None
    if not isinstance(value,dict):
        raise RuntimeError("Invalid provider response")
    return value


def https_health(url, *, allowed_host):
    u=urllib.parse.urlsplit(url)
    if (u.scheme!="https" or u.hostname!=allowed_host or u.port not in (None,443)
            or u.username or u.password or u.fragment):
        raise ValueError("Health endpoint must use approved HTTPS hostname")
    req=urllib.request.Request(url,headers={"Accept":"application/json"})
    try:
        with urllib.request.urlopen(req,timeout=8,context=ssl.create_default_context()) as resp:
            resp.read(4096)
            if resp.status!=200: raise RuntimeError("Health probe HTTP status not 200")
    except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError):
        raise RuntimeError("Deployment health endpoint unavailable") from None


class CloudflareVersions:
    def __init__(self,config,credentials,request=https_json):
        self.c=config;self.creds=credentials;self.request=request
        a=config.get("account_id");w=config.get("script")
        if not isinstance(a,str) or not re.fullmatch(r"[0-9a-f]{32}",a):
            raise ValueError("Invalid Cloudflare account")
        if not isinstance(w,str) or not WORKER.fullmatch(w):
            raise ValueError("Invalid Worker script")
        self.url=f"https://api.cloudflare.com/client/v4/accounts/{a}/workers/scripts/{w}/deployments"
    def _call(self,method,path="",payload=None):
        out=self.request(method,self.url+path,token=self.creds.token(self.c["credential"]),
                         payload=payload)
        if out.get("success") is not True or not isinstance(out.get("result"),(dict,list)):
            raise RuntimeError("Cloudflare deployment API did not confirm success")
        return out["result"]
    def current(self):
        deployments=self._call("GET")
        if isinstance(deployments,dict):
            deployments=deployments.get("deployments",[])
        if not isinstance(deployments,list) or not deployments:
            raise RuntimeError("No rollback-capable current Worker deployment")
        versions=deployments[0].get("versions",[])
        if len(versions)!=1 or versions[0].get("percentage")!=100:
            raise RuntimeError("Split traffic deployment requires manual release review")
        return versions[0]["version_id"]
    def promote(self,version):
        from cost_policy import deny_unverified_external_work
        deny_unverified_external_work("cloud provider promotion")
        try: uuid.UUID(version)
        except (TypeError,ValueError): raise ValueError("Invalid immutable Worker version") from None
        out=self._call("POST",payload={"strategy":"percentage",
                          "versions":[{"percentage":100,"version_id":version}]})
        if not out.get("id"):
            raise RuntimeError("Provider did not return Worker deployment ID")
        return {"provider_deployment_id":str(out["id"]),"version":version}
    def rollback(self,version):
        return self.promote(version)


class VercelAlias:
    """Explicit prebuilt deployment ID to allowlisted domain (not hosted CI)."""
    def __init__(self,config,credentials,request=https_json):
        self.c=config;self.creds=credentials;self.request=request
        name=config.get("alias")
        if not isinstance(name,str) or not HOST.fullmatch(name) or "." not in name:
            raise ValueError("Invalid Vercel target alias")
        self.alias=name
        self.team=config.get("team_id","")
        if self.team and not DEPLOY_ID.fullmatch(self.team):
            raise ValueError("Invalid Vercel team")
    def _url(self, path):
        q=urllib.parse.urlencode({"teamId":self.team}) if self.team else ""
        return "https://api.vercel.com"+path+("?" +q if q else "")
    def _call(self,method,path,payload=None):
        return self.request(method,self._url(path),token=self.creds.token(self.c["credential"]),payload=payload)
    def current(self):
        out=self._call("GET","/v4/aliases/"+urllib.parse.quote(self.alias,safe=""))
        # Fail closed without a concrete deployment identifier to restore.
        deployment=out.get("deployment")
        if isinstance(deployment,dict):deployment=deployment.get("id")
        if not isinstance(deployment,str) or not DEPLOY_ID.fullmatch(deployment):
            raise RuntimeError("Cannot establish previous deployment for Vercel alias")
        return deployment
    def promote(self,version):
        from cost_policy import deny_unverified_external_work
        deny_unverified_external_work("cloud provider promotion")
        if not isinstance(version,str) or not DEPLOY_ID.fullmatch(version):
            raise ValueError("Invalid immutable Vercel deployment identifier")
        meta=self._call("GET","/v13/deployments/"+urllib.parse.quote(version,safe=""))
        if meta.get("readyState")!="READY" and meta.get("state")!="READY":
            raise RuntimeError("Vercel deployment is not READY")
        out=self._call("POST",f"/v2/deployments/{version}/aliases",payload={"alias":self.alias})
        if out.get("error") or not isinstance(out.get("uid"),str):
            raise RuntimeError("Vercel alias assignment not confirmed")
        return {"provider_deployment_id":version,"alias":self.alias}
    def rollback(self,version):
        return self.promote(version)


class SSHCompose:
    """Preapproved Compose file already on remote host; no shell from repo events."""
    def __init__(self,cfg,credentials,run=subprocess.run):
        self.c=cfg;self.credentials=credentials;self.run=run
        for key in ("host","user","compose","project","credential","known_hosts"):
            if key not in cfg or not isinstance(cfg[key],str):
                raise ValueError("Missing SSH Compose target")
        if (not HOST.fullmatch(cfg["host"]) or not IDENT.fullmatch(cfg["user"]) or
            not IDENT.fullmatch(cfg["project"]) or not PATH.fullmatch(cfg["compose"])):
            raise ValueError("Unsafe SSH target or Compose path")
        # Explicitly pinned local SSH host keys; no StrictHostKeyChecking=no.
        self.identity=credentials.file(cfg["credential"])
        self.known=credentials.file(cfg["known_hosts"])
    def command(self,action):
        if action not in ("config","images","pull","up","ps"):raise ValueError("Operation not allowlisted")
        cmds={"config":"config -q","images":"config --images","pull":"pull --quiet",
              "up":"up -d --no-build","ps":"ps --format json"}
        remote=("docker compose --project-name "+self.c["project"]+" -f "+
                self.c["compose"]+" "+cmds[action])
        return ["ssh","-o","BatchMode=yes","-o","StrictHostKeyChecking=yes",
                "-o","UserKnownHostsFile="+str(self.known),
                "-o","IdentitiesOnly=yes","-o","ConnectTimeout=10",
                "-i",str(self.identity),
                self.c["user"]+"@"+self.c["host"],remote]
    def execute(self,action):
        r=self.run(self.command(action),check=False,timeout=80,
                   stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                   stdin=subprocess.DEVNULL,text=True)
        if r.returncode:raise RuntimeError("Remote Compose "+action+" failed")
        if action=="images":
            return {"images":[line.strip() for line in r.stdout.splitlines() if line.strip()]}
        return {"operation":action,"status":"ok"}
