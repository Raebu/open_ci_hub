#!/usr/bin/env python3
"""Open CI Hub: free-only, fail-closed AI task router and metadata-only monitor.

No unattended prompts, commercial production on developer trials, or paid fallback.
No secrets stored in source, logs, CLI args or on remote Samsung hardware.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPSHandler, HTTPRedirectHandler, ProxyHandler, build_opener

from cloud_ai_providers import PROVIDERS, activation_state, has_key, _read_key

STATE_DIR = Path.home() / ".local/state/open-ci-hub/free-ai-router"
MONITOR_FILE = "monitor.json"
USAGE_FILE = "usage.json"
ATTEST_FILE = "tier-proof.json"
MODEL = {key: provider.model for key, provider in PROVIDERS.items()}
TASKS = ("coding", "reasoning", "planning", "analysis", "extraction", "translation", "classification", "chat")
TASK_OPTIONS = ("auto",) + TASKS
MAX_PROMPT = 6000
MAX_RESPONSE_TOKENS = 256
METADATA_STALE_SECONDS = 45 * 60
OPENROUTER_DAILY_CAP = 12        # Lower than documented free 50/day
NVIDIA_DAILY_CAP = 8             # Prototyping only, never commercial execution
OTHER_DAILY_CAP = 6              # Free account proof required
MIN_INTERVAL_SECONDS = 75        # Per-provider spacing across all processes
COOLDOWN_SECONDS = 60 * 60
ENDPOINTS = {
    "openrouter": "https://openrouter.ai/api/v1/models",
    "nvidia": "https://integrate.api.nvidia.com/v1/models",
    "groq": "https://api.groq.com/openai/v1/models",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
}
REQUIRED_PRICING = {"prompt": "0", "completion": "0"}
SAFE_MODELS = {p: MODEL[p] for p in PROVIDERS}

class BlockRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def utc_now():
    return int(time.time())

def _store_dir():
    for p in (STATE_DIR.parent.parent, STATE_DIR.parent, STATE_DIR):
        if p.is_symlink():
            raise PermissionError("Symlinked router state path refused")
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    st=STATE_DIR.stat()
    if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise PermissionError("Router state directory requires owner-only 0700 permissions")
    return STATE_DIR

def _read(name):
    if name not in (MONITOR_FILE, USAGE_FILE, ATTEST_FILE):
        raise ValueError("Disallowed router state")
    path=_store_dir()/name
    if path.is_symlink(): raise PermissionError("Symlinked router state file refused")
    if not path.exists(): return {}
    st=path.stat()
    if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o600 or st.st_size>100000:
        raise PermissionError("Unsafe router state file")
    data=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data,dict): raise ValueError("Router state must be JSON object")
    return data

def _save(name,data):
    path=_store_dir()/name
    if path.is_symlink(): raise PermissionError("Symlinked router file refused")
    temp=_store_dir()/f".{name}.{os.getpid()}.tmp"
    fd=os.open(temp,os.O_WRONLY|os.O_EXCL|os.O_CREAT|getattr(os,"O_NOFOLLOW",0),0o600)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            json.dump(data,stream,sort_keys=True,indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:
        if temp.exists(): temp.unlink()

@contextmanager
def exclusive():
    path=_store_dir()/".lock"
    fd=os.open(path,os.O_RDWR|os.O_CREAT|getattr(os,"O_NOFOLLOW",0),0o600)
    try:
        st=os.fstat(fd)
        if st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o600:
            raise PermissionError("Unsafe state lock")
        fcntl.flock(fd,fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN)
        os.close(fd)

def _client():
    return build_opener(ProxyHandler({}), HTTPSHandler(), BlockRedirect())

def _metadata(provider):
    url=ENDPOINTS[provider]
    key=_read_key(provider)
    hdr={"Accept":"application/json","User-Agent":"OpenCIHub-FreeOnlyMonitor/1.0"}
    hdr["x-goog-api-key" if provider=="gemini" else "Authorization"] = (
        key if provider=="gemini" else "Bearer "+key)
    request=Request(url,headers=hdr)
    try:
        with _client().open(request,timeout=10) as res:
            raw=res.read(5000000+1)
            if len(raw)>5000000: return False,"catalog_too_large"
            payload=json.loads(raw)
    except HTTPError as exc:
        return False, ("provider_rate_limited" if exc.code==429 else "provider_http_"+str(exc.code))
    except (URLError,OSError,TimeoutError,ValueError,json.JSONDecodeError):
        return False,"metadata_unreachable"
    items=payload.get("models",payload.get("data",[]))
    if not isinstance(items,list): return False,"invalid_model_catalog"
    model=MODEL[provider]
    found=None
    for item in items:
        if not isinstance(item,dict): continue
        name=item.get("id",item.get("name",""))
        if name==model or name==f"models/{model}":
            found=item
            break
    if found is None: return False,"model_not_listed"
    if provider=="openrouter":
        price=found.get("pricing",{})
        if not isinstance(price,dict) or any(str(price.get(k,"unknown"))!="0" for k in REQUIRED_PRICING):
            return False,"nonzero_or_unverified_pricing"
        return True,"zero_price_catalog_confirmed"
    if provider=="nvidia": return True,"model_present_developer_prototype_only"
    return True,"model_present_billing_tier_unverified"

def _tier_verified(provider,now):
    """For Gemini/Groq require owner's *recent* verification of no paid billing."""
    if provider not in ("gemini","groq"): return False
    proof=_read(ATTEST_FILE).get(provider,{})
    return (isinstance(proof,dict) and proof.get("tier")=="free"
            and proof.get("no_paid_billing") is True
            and isinstance(proof.get("expires"),int) and proof["expires"]>now
            and proof["expires"]<=now+24*3600)

def _safe_activation(provider):
    try:
        return (has_key(provider) and
                provider in activation_state() and
                activation_state()[provider].get("model")==MODEL[provider])
    except (OSError,ValueError,RuntimeError,PermissionError):
        return False

def check_status(now=None):
    now=utc_now() if now is None else now
    states=_read(MONITOR_FILE)
    results=[]
    for p in PROVIDERS:
        item=states.get(p,{})
        checked=item.get("checked_at",0)
        eligible=(isinstance(checked,int) and 0<=now-checked<=METADATA_STALE_SECONDS
                  and item.get("metadata_ok") is True)
        reason=item.get("reason") or "not_checked"
        if not _safe_activation(p):
            eligible=False;reason="unactivated_or_missing_key"
        elif p in ("gemini","groq") and not _tier_verified(p,now):
            eligible=False;reason="free_account_not_independently_proven"
        if p=="nvidia":
            reason += " (prototype only)"
        usage=_read(USAGE_FILE).get(p,{})
        until=int(usage.get("blocked_until",0) or 0)
        if now<until:
            eligible=False;reason="cooldown_until_"+datetime.fromtimestamp(until,timezone.utc).isoformat()
        today=datetime.fromtimestamp(now,timezone.utc).strftime("%Y-%m-%d")
        cap=OPENROUTER_DAILY_CAP if p=="openrouter" else NVIDIA_DAILY_CAP if p=="nvidia" else OTHER_DAILY_CAP
        count=int(usage.get("attempts",0)) if usage.get("day")==today else 0
        if count>=cap:
            eligible=False;reason="local_daily_request_limit"
        results.append({"provider":p,"model":MODEL[p],"eligible":bool(eligible),
                        "reason":reason,"checked_at":checked or None,
                        "owner_only_free_tier_proof":_tier_verified(p,now) if p in ("gemini","groq") else None})
    return results

def monitor(now=None, fetcher=None):
    """No text generation or billable inference; read-only provider model catalogs."""
    now=utc_now() if now is None else now
    fetcher=fetcher or _metadata
    with exclusive():
        states=_read(MONITOR_FILE)
        for p in PROVIDERS:
            if not _safe_activation(p):
                states[p]={"checked_at":now,"metadata_ok":False,"reason":"unactivated_or_missing_key"}
                continue
            try:
                ok,why=fetcher(p)
            except Exception:
                ok,why=False,"metadata_unreachable"
            states[p]={"checked_at":now,"metadata_ok":bool(ok),"reason":str(why)[:100]}
        _save(MONITOR_FILE,states)
    return check_status(now)

def attest_tier(provider,*,free,no_paid_billing,now=None):
    if provider not in ("gemini","groq"):
        raise ValueError("Only Gemini and Groq need independent free-tier attestation")
    if not free or not no_paid_billing:
        raise PermissionError("Cannot allow unattended inference without confirmed free-only account")
    now=utc_now() if now is None else now
    with exclusive():
        p=_read(ATTEST_FILE)
        p[provider]={"tier":"free","no_paid_billing":True,"expires":now+24*3600}
        _save(ATTEST_FILE,p)
    return {"provider":provider,"free_only_verified_until":datetime.fromtimestamp(now+24*3600,timezone.utc).isoformat()}

def _reserve(provider,now):
    """Reserve attempt atomically BEFORE any possible paid/network call."""
    with exclusive():
        usage=_read(USAGE_FILE)
        d=usage.get(provider,{})
        day=datetime.fromtimestamp(now,timezone.utc).strftime("%Y-%m-%d")
        attempts=int(d.get("attempts",0)) if d.get("day")==day else 0
        cap=OPENROUTER_DAILY_CAP if provider=="openrouter" else NVIDIA_DAILY_CAP if provider=="nvidia" else OTHER_DAILY_CAP
        if attempts>=cap: return False,"daily_limit"
        if now<int(d.get("blocked_until",0) or 0): return False,"cooldown"
        if now-int(d.get("last_attempt",0) or 0)<MIN_INTERVAL_SECONDS: return False,"rate_spaced"
        d.update({"day":day,"attempts":attempts+1,"last_attempt":now})
        usage[provider]=d
        _save(USAGE_FILE,usage)
        return True,"reserved"

def _block(provider,code,now):
    with exclusive():
        usage=_read(USAGE_FILE)
        d=usage.get(provider,{})
        d["blocked_until"]=now+COOLDOWN_SECONDS
        d["last_failure"]="rate_limited" if code==429 else "access_denied" if code==403 else "provider_error"
        usage[provider]=d
        _save(USAGE_FILE,usage)

def _cloud_request(provider,prompt,max_tokens):
    key=_read_key(provider)
    payload={"model":MODEL[provider],"messages":[{"role":"user","content":prompt}],
             "max_tokens":max_tokens,"stream":False}
    if provider=="groq": payload["reasoning_effort"]="low"
    if provider=="openrouter":
        # Provider-enforced zero-dollar price cap, even if the catalog changes.
        payload["provider"]={"max_price":{"prompt":0,"completion":0}}
    body=json.dumps(payload,separators=(",",":")).encode()
    req=Request(PROVIDERS[provider].endpoint,method="POST",data=body,
                headers={"Authorization":"Bearer "+key,"Content-Type":"application/json",
                         "Accept":"application/json","User-Agent":"OpenCIHub-FreeOnlyRouter/1.0"})
    try:
        with _client().open(req,timeout=35) as res:
            if res.status!=200: return None,"invalid_response",res.status
            raw=res.read(500000)
        obj=json.loads(raw)
        txt=str(obj["choices"][0]["message"].get("content") or "").strip()
        return (txt,"ok",200) if txt else (None,"empty_response",200)
    except HTTPError as e:
        return None,"http_"+str(e.code),e.code
    except (URLError,TimeoutError,OSError,ValueError,KeyError,IndexError,TypeError):
        return None,"network_or_response_error",0

def _local_request(prompt,max_tokens,runner=subprocess.run):
    """Only send prompt via SSH stdin; no CLI arguments, Git files or logs."""
    payload=json.dumps({"model":"Qwen3-0.6B-Q4_K_M.gguf",
         "messages":[{"role":"user","content":"/no_think\n"+prompt}],
         "max_tokens":max_tokens,
         "temperature":0,
         "chat_template_kwargs":{"enable_thinking":False}}).encode()
    try:
        out=runner(["tailscale","ssh","linux@samsung-book-go",
                   "python3 /opt/open-ci-hub/ai/oci-ai-request.py"],
                   input=payload,capture_output=True,timeout=48,check=False)
        if out.returncode: return None
        data=json.loads(out.stdout[:300000])
        if not data.get("ok"): return None
        reply=str(data.get("text") or "").strip()
        return reply or None
    except (OSError,TimeoutError,subprocess.TimeoutExpired,ValueError,KeyError,TypeError,json.JSONDecodeError):
        return None

def classify(prompt):
    """Bounded local text heuristics; no cloud classification request."""
    lower=prompt[:1500].lower()
    if re.search(r"\b(python|javascript|typescript|function|code|bug|debug|sql|commit|repository|unit test|refactor)\b",lower):
        return "coding"
    if re.search(r"\b(translate|translation|french|spanish|nepali|german)\b",lower):
        return "translation"
    if re.search(r"\b(extract|parse|pull fields|json schema|ocr)\b",lower):
        return "extraction"
    if re.search(r"\b(classify|classification|categorize|label this)\b",lower):
        return "classification"
    if re.search(r"\b(analy[sz]e|analysis|compare|evaluate|report|metrics)\b",lower):
        return "analysis"
    if re.search(r"\b(plan|roadmap|strategy|milestones|prioriti[sz]e)\b",lower):
        return "planning"
    if re.search(r"\b(reason|prove|deduce|logic|why does|trade-off)\b",lower):
        return "reasoning"
    return "chat"

def ranked(task,purpose):
    if task not in TASKS: raise ValueError("Unsupported task type")
    if task in ("coding","reasoning","planning","analysis"):
        return ["nvidia","openrouter","gemini","groq"] if purpose=="prototype" else ["openrouter","gemini","groq"]
    if task in ("classification","extraction","translation"):
        return ["groq","openrouter","gemini","nvidia"] if purpose=="prototype" else ["groq","openrouter","gemini"]
    return ["openrouter","gemini","groq","nvidia"] if purpose=="prototype" else ["openrouter","gemini","groq"]

def ask(task,prompt,*,purpose="production",data_class="private",allow_cloud=False,
        max_tokens=MAX_RESPONSE_TOKENS,now=None,local=_local_request,cloud=_cloud_request):
    if task not in TASK_OPTIONS or purpose not in ("prototype","production") or data_class not in ("private","public"):
        raise ValueError("Invalid routing classification")
    if not isinstance(prompt,str) or not 1<=len(prompt)<=MAX_PROMPT:
        raise ValueError("Prompt length outside safe bounds")
    if task=="auto":
        task=classify(prompt)
    if not isinstance(max_tokens,int) or not 1<=max_tokens<=MAX_RESPONSE_TOKENS:
        raise ValueError("Output token ceiling exceeded")
    now=utc_now() if now is None else now
    attempts=[]
    # Private tasks never leave the tailnet, even if caller enables cloud.
    if allow_cloud and data_class=="public":
        status={x["provider"]:x for x in check_status(now)}
        for p in ranked(task,purpose):
            item=status[p]
            if not item["eligible"]: continue
            # Avoid selecting developer prototyping rights for production tasks.
            if p=="nvidia" and purpose!="prototype": continue
            reserved,why=_reserve(p,now)
            if not reserved:
                attempts.append({"provider":p,"outcome":why}); continue
            reply,outcome,code=cloud(p,prompt,max_tokens)
            attempts.append({"provider":p,"outcome":outcome})
            if reply: return {"ok":True,"task":task,"source":p,"model":MODEL[p],"text":reply,
                              "attempts":attempts,"paid_fallback":False}
            if code in (401,402,403,429):
                _block(p,code,now)
    text=local(prompt,max_tokens)
    return {"ok":bool(text),"task":task,"source":"samsung-local","model":"Qwen3-0.6B-Q4_K_M",
            "text":text or "", "attempts":attempts, "paid_fallback":False}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    sub.add_parser("status")
    sub.add_parser("monitor")
    tier=sub.add_parser("verify-free-account",
        help="Owner confirms provider console shows FREE and NO PAID BILLING; expires in 24h")
    tier.add_argument("provider",choices=("gemini","groq"))
    tier.add_argument("--confirm-no-paid-billing",action="store_true")
    tier.add_argument("--confirm-free-tier",action="store_true")
    run=sub.add_parser("ask",help="Prompt from stdin only. No prompts in shell history.")
    run.add_argument("--task",default="auto",choices=TASK_OPTIONS)
    run.add_argument("--purpose",choices=("prototype","production"),default="production")
    run.add_argument("--data-class",choices=("private","public"),default="private")
    run.add_argument("--allow-cloud",action="store_true")
    run.add_argument("--max-tokens",type=int,default=MAX_RESPONSE_TOKENS)
    args=parser.parse_args(argv)
    try:
        if args.command=="status": result=check_status()
        elif args.command=="monitor": result=monitor()
        elif args.command=="verify-free-account":
            if not (args.confirm_free_tier and args.confirm_no_paid_billing):
                raise PermissionError("Check account billing console before free-only attestation")
            result=attest_tier(args.provider,free=True,no_paid_billing=True)
        else:
            if sys.stdin.isatty():
                raise PermissionError("Send prompt through stdin, not command line or terminal history")
            prompt=sys.stdin.read(MAX_PROMPT+1)
            result=ask(args.task,prompt,purpose=args.purpose,data_class=args.data_class,
                       allow_cloud=args.allow_cloud,max_tokens=args.max_tokens)
        print(json.dumps(result,ensure_ascii=False))
        return 0 if args.command!="ask" or result["ok"] else 2
    except (ValueError,RuntimeError,PermissionError,OSError) as e:
        print("Router refused: "+str(e),file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
