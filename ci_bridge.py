#!/usr/bin/env python3
"""Authenticated provider triggers scheduled through Open CI Hub's shared resource broker.

Only owner-authored, digest-pinned manifests are accepted. This module does not
interpret provider YAML or execute commands supplied by a webhook.
"""
from __future__ import annotations
import argparse, hashlib, hmac, json, os, re, shutil, subprocess, sys, threading, time, secrets
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from contextlib import nullcontext
from ci_network import VALID_MODES, public_web_proxy, ensure_static_bridge, docker_network_options
from urllib.parse import quote

from app_runtime import Broker
from resource_guard import ResourceGate

ROOT = Path(__file__).resolve().parent
STATE = Path(os.environ.get("OPEN_CI_BRIDGE_STATE", str(ROOT / ".local" / "ci-bridge")))
MANIFEST = Path(os.environ.get("OPEN_CI_BRIDGE_MANIFEST", str(STATE / "manifest.json")))
MAX_BODY = 1024 * 1024
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA = re.compile(r"^[a-f0-9]{40}$")
IDENT = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9./:_-]{1,180}@sha256:[a-f0-9]{64}$")
ALLOWED_EVENTS = {"push", "pull_request", "merge_request"}
LEASE_HEARTBEAT_SECONDS = 45
TERMINAL = {"passed", "failed", "stranded"}

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
    apps=set()
    for repo,cfg in data["repositories"].items():
        if not REPO.fullmatch(repo) or not isinstance(cfg,dict):
            raise ValueError("Invalid repository manifest entry.")
        if cfg.get("provider") not in ("github","gitlab") or not isinstance(cfg.get("secret_env"),str) or not IDENT.fullmatch(cfg["secret_env"]):
            raise ValueError("Each repository requires provider github/gitlab and a webhook secret environment variable name.")
        branches=cfg.get("branches")
        if not isinstance(branches,list) or not branches or any(not isinstance(b,str) or not BRANCH.fullmatch(b) or ".." in b or "//" in b or b.endswith(("/", ".")) for b in branches):
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
        if type(cfg.get("max_seconds",900)) is not int or not 30<=cfg.get("max_seconds",900)<=3600:
            raise ValueError("max_seconds must be an integer between 30 and 3600.")
        ram=cfg.get("ram_mb",1024)
        if type(ram) is not int or not 256<=ram<=8192:
            raise ValueError("ram_mb must be an integer between 256 and 8192.")
        network=cfg.get("network","offline")
        if network not in VALID_MODES:
            raise ValueError("CI network must be offline or full")
        if network=="full" and cfg.get("trusted_network_confirmed") is not True:
            raise PermissionError("Full public-Web egress requires explicit trusted-network approval")
        parallel=cfg.get("max_parallel",2)
        if type(parallel) is not int or not 1<=parallel<=8:
            raise ValueError("max_parallel must be an integer between 1 and 8.")
        app=app_for(repo)
        if app in apps: raise ValueError("Repository names collide after scheduler app mapping.")
        apps.add(app)
    return data

def app_for(repo):
    slug=re.sub(r"[^a-z0-9-]+","-",repo.lower()).strip("-")
    suffix=hashlib.sha256(repo.encode()).hexdigest()[:8]
    return ("ci-"+slug[:35].rstrip("-")+"-"+suffix)[:49]

def get_broker():
    # Deliberately use the same default Broker database as other local app jobs.
    # ResourceGate therefore accounts for other app work and observed native runners.
    return Broker()

def ensure_scheduler(manifest, broker=None):
    broker=broker or get_broker()
    for repo,cfg in manifest["repositories"].items():
        app=app_for(repo)
        with broker._db() as db:
            row=db.execute("SELECT tasks,enabled FROM apps WHERE app=?",(app,)).fetchone()
        if row is None:
            broker.register(app,["build"],confirm=True)
        elif json.loads(row["tasks"]) != ["build"] or not row["enabled"]:
            raise RuntimeError("Existing scheduler app conflicts with the CI bridge manifest: "+app)
        with broker._db() as db:
            profile=ResourceGate.profile(db,app,"build")
            limit=ResourceGate.app_limit(db,app)
        expected={"ram_mb":cfg.get("ram_mb",1024),"vram_mb":0,"gpu_index":None}
        if profile != expected:
            broker.set_task_budget(app,"build",expected["ram_mb"],0,None,confirm=True)
        desired=cfg.get("max_parallel",2)
        if limit != desired:
            broker.set_parallel(app,desired,confirm=True)
        with broker._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS ci_bridge_events(
                job_id TEXT PRIMARY KEY, provider TEXT NOT NULL, repo TEXT NOT NULL,
                sha TEXT NOT NULL, branch TEXT NOT NULL, event TEXT NOT NULL,
                status TEXT NOT NULL, detail TEXT, created REAL NOT NULL, updated REAL NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id))""")
    return broker

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
            return repo,data.get("after",""),ref.removeprefix("refs/heads/"),"push"
        if kind=="pull_request":
            action=data.get("action")
            pr=data.get("pull_request") or {}
            if action not in ("opened","synchronize","reopened","ready_for_review") or pr.get("draft") is True:
                raise ValueError("Pull request action is not runnable.")
            repo=(data.get("repository") or {}).get("full_name","")
            head=pr.get("head") or {}
            head_repo=(head.get("repo") or {}).get("full_name","")
            if not repo or head_repo.lower()!=repo.lower():
                raise PermissionError("Fork pull requests are not enabled for local execution.")
            # Apply branch policy to the target branch while testing the exact head SHA.
            base=pr.get("base") or {}
            return repo,head.get("sha",""),base.get("ref",""),"pull_request"
        raise ValueError("Unsupported GitHub event.")
    kind=headers.get("X-Gitlab-Event","")
    if kind=="Push Hook":
        repo=(data.get("project") or {}).get("path_with_namespace","")
        return repo,data.get("checkout_sha") or data.get("after",""),data.get("ref","").removeprefix("refs/heads/"),"push"
    if kind=="Merge Request Hook":
        attrs=data.get("object_attributes") or {}
        if attrs.get("action") not in ("open","reopen","update") or attrs.get("state")!="opened":
            raise ValueError("Merge request action is not runnable.")
        project=data.get("project") or {}
        if not project.get("id") or project.get("id") != attrs.get("target_project_id") or project.get("id") != attrs.get("source_project_id"):
            raise PermissionError("Cross-project merge requests are not enabled for local execution.")
        repo=project.get("path_with_namespace","")
        # Apply branch policy to the target branch while testing the exact MR head SHA.
        return repo,(attrs.get("last_commit") or {}).get("id",""),attrs.get("target_branch",""),"merge_request"
    raise ValueError("Unsupported GitLab event.")

def enqueue(provider, repo, sha, branch, event, manifest, broker=None):
    cfg=manifest["repositories"].get(repo)
    if not cfg or cfg["provider"] != provider: raise PermissionError("Repository is not allowlisted for this provider.")
    if branch not in cfg["branches"]: raise PermissionError("Branch is not allowlisted.")
    if not SHA.fullmatch(sha): raise ValueError("A full 40-character commit SHA is required.")
    if event not in ALLOWED_EVENTS: raise ValueError("Unsupported provider event.")
    if cfg.get("network","offline")=="full" and event!="push":
        raise PermissionError("Full CI public-Web access is only approved for protected-branch push events.")
    broker=ensure_scheduler(manifest,broker)
    app=app_for(repo)
    idempotency=hashlib.sha256(f"{provider}:{repo}:{sha}:{event}".encode()).hexdigest()
    payload={"provider":provider,"repo":repo,"sha":sha,"branch":branch,"event":event}
    wire=broker._json_value(payload)
    now=time.time()
    with broker._db() as db:
        db.execute("BEGIN IMMEDIATE")
        existing=db.execute("SELECT id,status FROM jobs WHERE app=? AND idempotency_key=?",(app,idempotency)).fetchone()
        if existing:
            db.commit()
            return {"job_id":existing["id"],"status":existing["status"],"duplicate":True}
        pending=db.execute("SELECT COUNT(*) FROM jobs WHERE app=? AND status='queued'",(app,)).fetchone()[0]
        total=db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','leased','stranded')").fetchone()[0]
        if pending>=100 or total>=10000:
            db.rollback()
            raise RuntimeError("CI queue capacity reached; existing jobs were preserved.")
        job=secrets.token_hex(16)
        db.execute("""INSERT INTO jobs(id,app,task,payload,status,attempts,created,updated,idempotency_key,priority)
                      VALUES(?,?,?,?,'queued',0,?,?,?,0)""",(job,app,"build",wire,now,now,idempotency))
        db.execute("""INSERT INTO ci_bridge_events(job_id,provider,repo,sha,branch,event,status,created,updated)
                      VALUES(?,?,?,?,?,?,'queued',?,?)""",(job,provider,repo,sha,branch,event,now,now))
        db.commit()
    return {"job_id":job,"status":"queued","duplicate":False}

def run_cmd(argv, *, cwd=None, timeout=120):
    return subprocess.run(argv,cwd=cwd,timeout=timeout,check=False,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                          stdin=subprocess.DEVNULL,text=True,errors="replace")

def tail(path, limit=12000):
    try:
        with path.open("rb") as f:
            f.seek(0,2); size=f.tell(); f.seek(max(0,size-limit),0)
            return f.read(limit).decode("utf-8","replace")
    except OSError:
        return ""

def provider_status(cfg, repo, sha, state, description, job_url=""):
    provider=cfg["provider"]
    if provider=="github":
        result=run_cmd(["gh","api","--method","POST",f"repos/{repo}/statuses/{sha}",
          "-f",f"state={state}","-f","context=Open CI Hub/bridge-smoke","-f",f"description={description[:140]}",
          "-f",f"target_url={job_url or 'https://github.com/'+repo+'/commit/'+sha}"],timeout=20)
    else:
        project=quote(repo,safe="")
        state_map={"pending":"pending","success":"success","failure":"failed","error":"failed"}
        result=run_cmd(["glab","api","--hostname","gitlab.com","-X","POST",
          f"projects/{project}/statuses/{sha}","-f",f"state={state_map.get(state,state)}",
          "-f","name=Open CI Hub/bridge-smoke","-f",f"description={description[:255]}"],timeout=20)
    return result.returncode==0

def event_update(broker, job_id, status, detail=None):
    with broker._db() as db:
        db.execute("UPDATE ci_bridge_events SET status=?,detail=?,updated=? WHERE job_id=?",
                   (status,str(detail or "")[:1200],time.time(),job_id))
        db.commit()

def claim_one(manifest, broker=None):
    broker=ensure_scheduler(manifest,broker)
    blocked=[]
    for repo,cfg in sorted(manifest["repositories"].items()):
        who={"app":app_for(repo),"role":"worker","tasks":["build"]}
        result=broker.claim(who)
        if result.get("job"):
            return result["job"],None
        reason=result.get("reason","queue_empty")
        if reason!="queue_empty": blocked.append(reason)
    # Prefer an explicit capacity reason over a misleading idle result.
    for reason in ("critical_thermal","critical_memory_pressure","low_battery","admissions_paused",
                   "ram_sensor_unavailable","native_runner_telemetry_unavailable","ram_headroom",
                   "global_parallel_limit","project_parallel_limit","integrated_gpu_busy"):
        if reason in blocked: return None,reason
    return None,(blocked[0] if blocked else "queue_empty")

def monitored_container(argv, *, cwd, timeout, log_path, heartbeat, container_name):
    deadline=time.monotonic()+timeout
    log_path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    lost_lease=False
    with log_path.open("ab") as stream:
        proc=subprocess.Popen(argv,cwd=cwd,stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,
                              start_new_session=True)
        next_heartbeat=time.monotonic()+LEASE_HEARTBEAT_SECONDS
        while proc.poll() is None:
            now=time.monotonic()
            if now>=deadline:
                try: run_cmd(["docker","kill",container_name],timeout=5)
                except (OSError,subprocess.TimeoutExpired): pass
                proc.terminate()
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired: proc.kill();proc.wait(timeout=5)
                stream.write(b"\nOpen CI Hub: step timed out; container termination requested.\n");stream.flush()
                return 124,False
            if now>=next_heartbeat:
                if not heartbeat():
                    lost_lease=True
                    try: run_cmd(["docker","kill",container_name],timeout=5)
                    except (OSError,subprocess.TimeoutExpired): pass
                    proc.terminate()
                    try: proc.wait(timeout=5)
                    except subprocess.TimeoutExpired: proc.kill();proc.wait(timeout=5)
                    stream.write(b"\nOpen CI Hub: worker lease heartbeat failed; container termination requested.\n");stream.flush()
                    return 125,True
                next_heartbeat=time.monotonic()+LEASE_HEARTBEAT_SECONDS
            time.sleep(1)
        return proc.returncode, lost_lease

def execute_one(job, manifest, broker=None):
    broker=ensure_scheduler(manifest,broker)
    payload=job["payload"]
    repo=payload["repo"];cfg=manifest["repositories"][repo]
    who={"app":app_for(repo),"role":"worker","tasks":["build"]}
    job_id=job["job_id"];token=job["lease_token"]
    work=STATE/"work"/job_id
    log=STATE/"logs"/(job_id+".log")
    event_update(broker,job_id,"running","Claimed by shared resource-aware scheduler.")
    try:
        provider_status(cfg,repo,payload["sha"],"pending","Open CI Hub local build queued",f"https://github.com/{repo}/commit/{payload['sha']}")
    except (OSError,subprocess.TimeoutExpired):
        pass
    passed=False;detail="";lease_lost=False
    try:
        if cfg.get("network","offline")=="full" and payload["event"]!="push":
            raise RuntimeError("Network privilege changed since queue admission; rejected for safety")
        work.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if work.exists(): shutil.rmtree(work)
        work.mkdir(mode=0o700)
        url=f"https://github.com/{repo}.git" if cfg["provider"]=="github" else f"https://gitlab.com/{repo}.git"
        clone=run_cmd(["git","clone","--no-checkout","--filter=blob:none",url,str(work)],timeout=120)
        if clone.returncode: raise RuntimeError("Repository checkout failed: "+clone.stdout[-1200:])
        checkout=run_cmd(["git","-C",str(work),"checkout","--detach",payload["sha"]],timeout=120)
        if checkout.returncode: raise RuntimeError("Exact SHA checkout failed: "+checkout.stdout[-1200:])
        got=run_cmd(["git","-C",str(work),"rev-parse","HEAD"],timeout=10)
        if got.returncode or got.stdout.strip()!=payload["sha"]:
            raise RuntimeError("Checked-out commit SHA did not match trigger.")
        image=cfg["image"]
        inspect=run_cmd(["docker","image","inspect",image],timeout=15)
        if inspect.returncode:
            raise RuntimeError("Digest-pinned build image is not present locally; owner must provision it.")
        budget=max(256,int(job["resources"].get("effective_ram_mb",cfg.get("ram_mb",1024))))
        for step_index,step in enumerate(cfg["steps"]):
            container_name=f"open-ci-{job_id[:20]}-{step_index}"
            argv=["docker","run","--rm","--name",container_name,"--network","none","--read-only",
                  "--tmpfs","/tmp:rw,nosuid,nodev,size=256m","--cap-drop=ALL",
                  "--security-opt=no-new-privileges","--pids-limit=128",
                  "--memory",f"{budget}m","--memory-swap",f"{budget}m","--cpus","1.0",
                  "--user",f"{os.getuid()}:{os.getgid()}","-e","HOME=/tmp","-e","CI=true",
                  "-v",f"{work}:/workspace:rw","-w","/workspace"]
            enabled=cfg.get("network","offline")=="full"
            context=public_web_proxy(STATE,secrets.token_hex(16)) if enabled else nullcontext(None)
            def heartbeat():
                try:
                    broker.heartbeat(who,job_id,token)
                    return True
                except (PermissionError,RuntimeError,OSError):
                    return False
            with context as egress:
                if enabled:
                    binary=ensure_static_bridge(ROOT/"ci_proxy_bridge.c",STATE)
                    argv+=docker_network_options(binary,egress)
                argv.extend([image,*step["argv"]])
                code,lease_lost=monitored_container(argv,cwd=ROOT,timeout=cfg.get("max_seconds",900),
                                                    log_path=log,heartbeat=heartbeat,container_name=container_name)
            if lease_lost:
                detail="Worker lease was lost; job remains reserved until safe recovery."
                raise RuntimeError(detail)
            if code:
                raise RuntimeError(f"Step {step['name']} failed (exit {code}): "+tail(log,3000))
        passed=True;detail="All approved local steps passed."
    except (RuntimeError,OSError,subprocess.TimeoutExpired) as exc:
        detail=str(exc)[:1200]
    finally:
        if work.exists(): shutil.rmtree(work,ignore_errors=True)
    if lease_lost:
        event_update(broker,job_id,"stranded",detail)
        return {"job_id":job_id,"status":"stranded","detail":detail,"commit":payload["sha"]}
    try:
        completion=broker.complete(who,job_id,token,passed,{"status":"passed" if passed else "failed","detail":detail[:250]})
    except (PermissionError,RuntimeError,ValueError) as exc:
        event_update(broker,job_id,"stranded","Could not safely release worker lease: "+str(exc))
        return {"job_id":job_id,"status":"stranded","detail":detail,"commit":payload["sha"]}
    state=completion["status"]
    if state=="completed":
        event_state="passed";provider_state="success"
    elif state=="queued":
        event_state="queued";provider_state="pending"
        detail="Attempt failed and has been queued for a safe retry: "+detail
    else:
        event_state="failed";provider_state="failure"
    event_update(broker,job_id,event_state,detail)
    try:
        reported=provider_status(cfg,repo,payload["sha"],provider_state,detail,
                                 f"https://github.com/{repo}/commit/{payload['sha']}")
    except (OSError,subprocess.TimeoutExpired):
        reported=False
    return {"job_id":job_id,"status":event_state,"detail":detail,"commit":payload["sha"],
            "attempt":job.get("attempt"),"provider_status_reported":reported}

def worker_once(manifest=None,broker=None):
    manifest=manifest or load_manifest()
    broker=ensure_scheduler(manifest,broker)
    job,reason=claim_one(manifest,broker)
    if not job: return {"status":"idle" if reason=="queue_empty" else "blocked","reason":reason}
    return execute_one(job,manifest,broker)

def worker_loop(manifest,broker,poll_seconds=5,stop_event=None):
    stop_event=stop_event or threading.Event()
    while not stop_event.is_set():
        try:
            result=worker_once(manifest,broker)
            if result.get("status") in ("idle","blocked"):
                stop_event.wait(poll_seconds)
            else:
                print(json.dumps(result,ensure_ascii=True),flush=True)
        except (OSError,RuntimeError,ValueError) as exc:
            print(json.dumps({"status":"worker-error","detail":str(exc)[:300]}),file=sys.stderr,flush=True)
            stop_event.wait(max(5,poll_seconds))

def status(broker=None):
    broker=broker or get_broker()
    with broker._db() as db:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ci_bridge_events'").fetchone()
        if not exists:return []
        rows=db.execute("""SELECT e.job_id,e.provider,e.repo,e.sha,e.branch,e.event,e.status,e.detail,
                                  e.created,e.updated,j.attempts,j.status AS scheduler_status
                           FROM ci_bridge_events e JOIN jobs j ON j.id=e.job_id
                           ORDER BY e.created DESC LIMIT 100""").fetchall()
        return [dict(row) for row in rows]

def serve(host="127.0.0.1",port=8766):
    manifest=load_manifest()
    broker=ensure_scheduler(manifest)
    class Handler(BaseHTTPRequestHandler):
        server_version="OpenCIHubTrigger/2"
        def log_message(self,*args): pass
        def response(self,code,data):
            payload=json.dumps(data).encode()
            self.send_response(code);self.send_header("Content-Type","application/json")
            self.send_header("Cache-Control","no-store");self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Content-Length",str(len(payload)));self.end_headers();self.wfile.write(payload)
        def do_GET(self):
            if self.path!="/health": self.response(404,{"error":"not found"});return
            self.response(200,{"ok":True,"service":"open-ci-hub-trigger","scheduler":"shared-resource-broker"})
        def do_POST(self):
            try:
                if self.path not in ("/webhook/github","/webhook/gitlab"): raise ValueError("Unknown route.")
                provider=self.path.rsplit("/",1)[1]
                size=int(self.headers.get("Content-Length","0"))
                if not 1<=size<=MAX_BODY: raise ValueError("Invalid webhook body size.")
                body=self.rfile.read(size)
                data=json.loads(body)
                if not isinstance(data,dict): raise ValueError("Webhook JSON must be an object.")
                repo=((data.get("repository") or {}).get("full_name","") if provider=="github"
                      else (data.get("project") or {}).get("path_with_namespace",""))
                cfg=manifest["repositories"].get(repo)
                if not cfg or cfg["provider"]!=provider: raise PermissionError("Repository not allowlisted.")
                verify_signature(provider,os.environ.get(cfg["secret_env"],""),body,self.headers)
                repo,sha,branch,event=parse_event(provider,self.headers,body)
                out=enqueue(provider,repo,sha,branch,event,manifest,broker)
                self.response(202,out)
            except (ValueError,PermissionError,KeyError,TypeError) as exc:
                self.response(403 if isinstance(exc,PermissionError) else 400,{"error":str(exc)[:200]})
            except RuntimeError as exc:
                self.response(503,{"error":str(exc)[:200]})
    ThreadingHTTPServer((host,port),Handler).serve_forever()

def main():
    p=argparse.ArgumentParser()
    sub=p.add_subparsers(dest="cmd",required=True)
    sub.add_parser("init-state");sub.add_parser("status")
    s=sub.add_parser("serve");s.add_argument("--host",default="127.0.0.1");s.add_argument("--port",type=int,default=8766)
    one=sub.add_parser("worker-once")
    loop=sub.add_parser("worker");loop.add_argument("--workers",type=int,default=3);loop.add_argument("--poll-seconds",type=int,default=5)
    a=p.parse_args()
    try:
        if a.cmd=="init-state":
            private_dir(STATE);manifest=load_manifest();broker=ensure_scheduler(manifest)
            out={"ok":True,"state":str(STATE),"scheduler":"shared-resource-broker",
                 "resources":broker.resource_status(),"repositories":list(manifest["repositories"])}
        elif a.cmd=="status": out=status()
        elif a.cmd=="serve": serve(a.host,a.port);return 0
        elif a.cmd=="worker-once": out=worker_once()
        else:
            if type(a.workers) is not int or not 1<=a.workers<=8 or not 1<=a.poll_seconds<=60:
                raise ValueError("workers must be 1–8 and poll-seconds 1–60.")
            manifest=load_manifest();broker=ensure_scheduler(manifest)
            stop_event=threading.Event()
            with ThreadPoolExecutor(max_workers=a.workers,thread_name_prefix="ci-worker") as pool:
                futures=[pool.submit(worker_loop,manifest,broker,a.poll_seconds,stop_event) for _ in range(a.workers)]
                try:
                    for future in futures: future.result()
                except KeyboardInterrupt:
                    stop_event.set()
                    print("Open CI Hub: stopping new claims; active jobs are allowed to finish.",file=sys.stderr,flush=True)
                    for future in futures:
                        try: future.result()
                        except Exception: pass
            return 0
        print(json.dumps(out,indent=2,ensure_ascii=True));return 0
    except (ValueError,PermissionError,RuntimeError,OSError) as exc:
        print("Open CI Hub bridge:",str(exc)[:300],file=sys.stderr);return 1
if __name__=="__main__": raise SystemExit(main())
