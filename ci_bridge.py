#!/usr/bin/env python3
"""Open CI Hub-owned trigger queue and constrained local execution worker.

This is deliberately not a GitHub Actions/GitLab YAML interpreter. Only repositories,
branches, image digests, and argv steps in a private owner-authored manifest are accepted.
Provider webhooks enqueue immutable commit SHAs; a local worker checks out that exact SHA,
runs allowlisted steps in a disposable container, and publishes a commit status.
"""
from __future__ import annotations
import argparse, hashlib, hmac, json, os, re, secrets, shutil, signal, sqlite3, subprocess, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
STATE = Path(os.environ.get("OPEN_CI_BRIDGE_STATE", str(ROOT / ".local" / "ci-bridge")))
MANIFEST = Path(os.environ.get("OPEN_CI_BRIDGE_MANIFEST", str(STATE / "manifest.json")))
DB = STATE / "queue.sqlite3"
MAX_BODY = 1024 * 1024
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA = re.compile(r"^[a-f0-9]{40}$")
IDENT = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9./:_-]{1,180}@sha256:[a-f0-9]{64}$")
ALLOWED_EVENTS = {"push", "pull_request", "merge_request"}
TERMINAL = {"passed", "failed", "cancelled"}

def private_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or os.name == "posix" and path.stat().st_mode & 0o077:
        raise PermissionError("CI bridge state must be a private, non-symlink directory.")
    if os.name == "posix":
        os.chmod(path, 0o700)

def load_manifest(path=MANIFEST):
    if path.is_symlink() or not path.is_file() or (os.name == "posix" and path.stat().st_mode & 0o077):
        raise PermissionError("Owner manifest must exist, be a regular file, and have mode 0600.")
    if os.name == "posix" and path.stat().st_uid != os.getuid():
        raise PermissionError("Owner manifest must belong to the service user.")
    data=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data,dict) or data.get("schema") != 1 or not isinstance(data.get("repositories"),dict):
        raise ValueError("Manifest schema must be 1 and contain repositories.")
    for repo,cfg in data["repositories"].items():
        if not REPO.fullmatch(repo) or not isinstance(cfg,dict):
            raise ValueError("Invalid repository manifest entry.")
        if cfg.get("provider") not in ("github","gitlab") or not isinstance(cfg.get("secret_env"),str) or not IDENT.fullmatch(cfg["secret_env"]):
            raise ValueError("Each repository requires provider github/gitlab and a webhook secret environment variable name.")
        branches=cfg.get("branches")
        if not isinstance(branches,list) or not branches or any(not isinstance(b,str) or not IDENT.fullmatch(b) for b in branches):
            raise ValueError("Each repository requires explicit allowed branch names.")
        image=cfg.get("image")
        if not isinstance(image,str) or not IMAGE.fullmatch(image):
            raise ValueError("Build image must be pinned by sha256 digest.")
        steps=cfg.get("steps")
        if not isinstance(steps,list) or not 1<=len(steps)<=30:
            raise ValueError("Each repository needs 1–30 locally approved steps.")
        for step in steps:
            if (not isinstance(step,dict) or not isinstance(step.get("name"),str) or not IDENT.fullmatch(step["name"])
                or not isinstance(step.get("argv"),list) or not 1<=len(step["argv"])<=30
                or any(not isinstance(a,str) or not a or len(a)>1000 or "\x00" in a for a in step["argv"])):
                raise ValueError("Invalid locally approved argv step.")
        if cfg.get("max_seconds",900) not in range(30,3601):
            raise ValueError("max_seconds must be between 30 and 3600.")
    return data

def connect():
    private_dir(STATE)
    db=sqlite3.connect(DB,timeout=5)
    if os.name == "posix": os.chmod(DB,0o600) if DB.exists() else None
    db.row_factory=sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("""CREATE TABLE IF NOT EXISTS jobs(
      id TEXT PRIMARY KEY, provider TEXT NOT NULL, repo TEXT NOT NULL, sha TEXT NOT NULL,
      branch TEXT NOT NULL, event TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
      created REAL NOT NULL, updated REAL NOT NULL, detail TEXT, UNIQUE(provider,repo,sha,event))""")
    db.commit()
    if os.name == "posix": os.chmod(DB,0o600)
    return db

def enqueue(provider, repo, sha, branch, event, manifest):
    cfg=manifest["repositories"].get(repo)
    if not cfg or cfg["provider"] != provider: raise PermissionError("Repository is not allowlisted for this provider.")
    if branch not in cfg["branches"]: raise PermissionError("Branch is not allowlisted.")
    if not SHA.fullmatch(sha): raise ValueError("A full 40-character commit SHA is required.")
    if event not in ALLOWED_EVENTS: raise ValueError("Unsupported provider event.")
    now=time.time()
    with connect() as db:
        existing=db.execute("SELECT id,status FROM jobs WHERE provider=? AND repo=? AND sha=? AND event=?",(provider,repo,sha,event)).fetchone()
        if existing: return {"job_id":existing["id"],"status":existing["status"],"duplicate":True}
        job=secrets.token_hex(16)
        db.execute("INSERT INTO jobs(id,provider,repo,sha,branch,event,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                   (job,provider,repo,sha,branch,event,"queued",now,now))
        db.commit()
    return {"job_id":job,"status":"queued","duplicate":False}

def verify_signature(provider, secret, body, headers):
    if not secret or len(secret)<32: raise PermissionError("Webhook secret must be at least 32 characters.")
    if provider=="github":
        supplied=headers.get("X-Hub-Signature-256","")
        expected="sha256="+hmac.new(secret.encode(),body,hashlib.sha256).hexdigest()
    else:
        supplied=headers.get("X-Gitlab-Token","")
        expected=secret
    if not supplied or not hmac.compare_digest(supplied,expected):
        raise PermissionError("Provider webhook authentication failed.")

def parse_event(provider, headers, body):
    try: data=json.loads(body)
    except (ValueError,UnicodeError): raise ValueError("Webhook body is not valid JSON.") from None
    if not isinstance(data,dict): raise ValueError("Webhook JSON must be an object.")
    if provider=="github":
        kind=headers.get("X-GitHub-Event","")
        if kind=="push":
            repo=(data.get("repository") or {}).get("full_name","")
            ref=data.get("ref","")
            branch=ref.removeprefix("refs/heads/")
            return repo,data.get("after",""),branch,"push"
        if kind=="pull_request":
            action=data.get("action")
            pr=data.get("pull_request") or {}
            if action not in ("opened","synchronize","reopened","ready_for_review") or pr.get("draft") is True:
                raise ValueError("Pull request action is not runnable.")
            repo=(data.get("repository") or {}).get("full_name","")
            head=pr.get("head") or {}
            return repo,head.get("sha",""),(head.get("ref","")),"pull_request"
        raise ValueError("Unsupported GitHub event.")
    kind=headers.get("X-Gitlab-Event","")
    if kind=="Push Hook":
        repo=(data.get("project") or {}).get("path_with_namespace","")
        return repo,data.get("checkout_sha") or data.get("after",""),(data.get("ref","").removeprefix("refs/heads/")),"push"
    if kind=="Merge Request Hook":
        attrs=data.get("object_attributes") or {}
        if attrs.get("action") not in ("open","reopen","update") or attrs.get("state")!="opened":
            raise ValueError("Merge request action is not runnable.")
        repo=(data.get("project") or {}).get("path_with_namespace","")
        return repo,attrs.get("last_commit",{}).get("id",""),attrs.get("source_branch",""),"merge_request"
    raise ValueError("Unsupported GitLab event.")

def run_cmd(argv, *, cwd=None, timeout=120, env=None):
    return subprocess.run(argv,cwd=cwd,timeout=timeout,check=False,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                          env=env,text=True,errors="replace")

def provider_status(cfg, repo, sha, state, description, job_url=""):
    # Status writes happen on the host after the build container exits; tokens are never mounted into it.
    provider=cfg["provider"]
    if provider=="github":
        result=run_cmd(["gh","api","--method","POST",f"repos/{repo}/statuses/{sha}",
          "-f",f"state={state}","-f","context=Open CI Hub/local","-f",f"description={description[:140]}",
          "-f",f"target_url={job_url}"],timeout=20)
    else:
        project=quote(repo,safe="")
        state_map={"pending":"pending","success":"success","failure":"failed","error":"failed"}
        result=run_cmd(["glab","api","--hostname","gitlab.com","-X","POST",
          f"projects/{project}/statuses/{sha}","-f",f"state={state_map.get(state,state)}",
          "-f","name=Open CI Hub/local","-f",f"description={description[:255]}"],timeout=20)
    return result.returncode==0

def execute_one(job, manifest):
    cfg=manifest["repositories"][job["repo"]]
    with connect() as db:
        db.execute("UPDATE jobs SET status='running',attempts=attempts+1,updated=? WHERE id=? AND status='queued'",(time.time(),job["id"]))
        db.commit()
    work=STATE/"work"/job["id"]
    work.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    try:
        if work.exists(): shutil.rmtree(work)
        work.mkdir(mode=0o700)
        url=f"https://github.com/{job['repo']}.git" if cfg["provider"]=="github" else f"https://gitlab.com/{job['repo']}.git"
        clone=run_cmd(["git","clone","--no-checkout","--filter=blob:none",url,str(work)],timeout=120)
        if clone.returncode: raise RuntimeError("Repository checkout failed: "+clone.stdout[-1200:])
        checkout=run_cmd(["git","-C",str(work),"checkout","--detach",job["sha"]],timeout=120)
        if checkout.returncode: raise RuntimeError("Exact SHA checkout failed: "+checkout.stdout[-1200:])
        got=run_cmd(["git","-C",str(work),"rev-parse","HEAD"],timeout=10)
        if got.returncode or got.stdout.strip()!=job["sha"]: raise RuntimeError("Checked-out commit SHA did not match trigger.")
        image=cfg["image"]
        pull=run_cmd(["docker","image","inspect",image],timeout=15)
        if pull.returncode: raise RuntimeError("Digest-pinned build image is not already present locally; owner must provision it.")
        for step in cfg["steps"]:
            argv=["docker","run","--rm","--network","none","--read-only","--tmpfs","/tmp:rw,nosuid,nodev,size=256m",
                  "--cap-drop=ALL","--security-opt=no-new-privileges","--pids-limit=128","--memory=2g","--cpus=1.5",
                  "--user",f"{os.getuid()}:{os.getgid()}","-v",f"{work}:/workspace:rw","-w","/workspace",
                  image,*step["argv"]]
            result=run_cmd(argv,cwd=ROOT,timeout=cfg.get("max_seconds",900))
            log=STATE/"logs"/(job["id"]+".log");log.parent.mkdir(mode=0o700,exist_ok=True)
            with log.open("a",encoding="utf-8") as stream:
                stream.write(f"\n## {step['name']} (exit {result.returncode})\n")
                stream.write(result.stdout[-20000:])
                stream.flush()
            if result.returncode: raise RuntimeError(f"Step {step['name']} failed (exit {result.returncode}).")
        status="passed";detail="All approved local steps passed."
    except (RuntimeError,OSError,subprocess.TimeoutExpired) as exc:
        status="failed";detail=str(exc)[:1200]
    finally:
        # Preserve bounded logs and remove source/workspace after execution.
        if work.exists(): shutil.rmtree(work,ignore_errors=True)
    with connect() as db:
        db.execute("UPDATE jobs SET status=?,detail=?,updated=? WHERE id=?",(status,detail,time.time(),job["id"]))
        db.commit()
    try: provider_status(cfg,job["repo"],job["sha"],"success" if status=="passed" else "failure",
                         detail[:140],f"https://github.com/Raebu/open_ci_hub")
    except (OSError,subprocess.TimeoutExpired): pass
    return {"job_id":job["id"],"status":status,"detail":detail,"commit":job["sha"]}

def claim_one():
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row=db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
        if not row: db.commit(); return None
        db.execute("UPDATE jobs SET status='running',attempts=attempts+1,updated=? WHERE id=? AND status='queued'",(time.time(),row["id"]))
        db.commit()
        return dict(row)

def serve(host="127.0.0.1",port=8766):
    manifest=load_manifest()
    class Handler(BaseHTTPRequestHandler):
        server_version="OpenCIHubTrigger/1"
        def log_message(self,*args): pass
        def do_GET(self):
            if self.path!="/health": self.send_error(404); return
            self.send_response(200);self.send_header("Content-Type","application/json");self.end_headers()
            self.wfile.write(b'{"ok":true,"service":"open-ci-hub-trigger"}')
        def do_POST(self):
            try:
                if self.path not in ("/webhook/github","/webhook/gitlab"): raise ValueError("Unknown route.")
                provider=self.path.rsplit("/",1)[1]
                size=int(self.headers.get("Content-Length","0"))
                if not 1<=size<=MAX_BODY: raise ValueError("Invalid webhook body size.")
                body=self.rfile.read(size)
                cfg_secret_env=None
                # Determine repo from authenticated body first; secret lookup is then scoped to that manifest entry.
                data=json.loads(body)
                repo=((data.get("repository") or {}).get("full_name","") if provider=="github"
                      else (data.get("project") or {}).get("path_with_namespace",""))
                if provider=="github" and self.headers.get("X-GitHub-Event")=="pull_request":
                    repo=(data.get("repository") or {}).get("full_name","")
                cfg=manifest["repositories"].get(repo)
                if not cfg or cfg["provider"]!=provider: raise PermissionError("Repository not allowlisted.")
                secret=os.environ.get(cfg["secret_env"],"")
                verify_signature(provider,secret,body,self.headers)
                repo,sha,branch,event=parse_event(provider,self.headers,body)
                out=enqueue(provider,repo,sha,branch,event,manifest)
                payload=json.dumps(out).encode()
                self.send_response(202);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(payload)));self.end_headers();self.wfile.write(payload)
            except (ValueError,PermissionError,KeyError,TypeError,json.JSONDecodeError) as exc:
                payload=json.dumps({"error":str(exc)[:200]}).encode()
                self.send_response(403 if isinstance(exc,PermissionError) else 400);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(payload)));self.end_headers();self.wfile.write(payload)
    ThreadingHTTPServer((host,port),Handler).serve_forever()

def worker_once():
    manifest=load_manifest()
    with connect() as db: row=db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
    if not row: return {"status":"idle"}
    job=dict(row); cfg=manifest["repositories"].get(job["repo"])
    if not cfg: return {"status":"blocked","job_id":job["id"],"reason":"repository removed from allowlist"}
    # execute_one owns the running transition.
    with connect() as db:
        db.execute("UPDATE jobs SET status='queued' WHERE id=? AND status='queued'",(job["id"],));db.commit()
    return execute_one(job,manifest)

def status():
    with connect() as db: rows=db.execute("SELECT id,provider,repo,sha,branch,event,status,attempts,created,updated,detail FROM jobs ORDER BY created DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]

def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest="cmd",required=True)
    sub.add_parser("init-state")
    sub.add_parser("status")
    s=sub.add_parser("serve");s.add_argument("--host",default="127.0.0.1");s.add_argument("--port",type=int,default=8766)
    sub.add_parser("worker-once")
    a=p.parse_args()
    try:
        if a.cmd=="init-state": private_dir(STATE);connect();out={"ok":True,"state":str(STATE)}
        elif a.cmd=="status": out=status()
        elif a.cmd=="serve": serve(a.host,a.port);return 0
        else: out=worker_once()
        print(json.dumps(out,indent=2));return 0
    except (ValueError,PermissionError,RuntimeError,OSError,sqlite3.Error) as exc:
        print("Open CI Hub bridge:",str(exc)[:300],file=sys.stderr);return 1
if __name__=="__main__": raise SystemExit(main())
