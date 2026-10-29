#!/usr/bin/env python3
"""Bounded local model request handler, stdin/stdout only via Tailscale SSH.

No listener, secrets, cloud APIs, filesystem writes or ambient shell execution.
"""
import json
import sys
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

def main():
    try:
        raw=sys.stdin.buffer.read(18000)
        if len(raw)>17000: raise ValueError("Request exceeds bound")
        d=json.loads(raw)
        if not isinstance(d,dict) or d.get("model")!="Qwen3-0.6B-Q4_K_M.gguf":
            raise ValueError("Unapproved local model")
        msgs=d.get("messages")
        if (not isinstance(msgs,list) or len(msgs)!=1 or
            not isinstance(msgs[0],dict) or msgs[0].get("role")!="user" or
            not isinstance(msgs[0].get("content"),str) or
            len(msgs[0]["content"])>6200):
            raise ValueError("Unsafe messages")
        max_tokens=d.get("max_tokens",0)
        if not isinstance(max_tokens,int) or not 1<=max_tokens<=256:
            raise ValueError("Output token cap exceeded")
        payload={"model":d["model"],"messages":msgs,"max_tokens":max_tokens,
                 "stream":False,"temperature":0,
                 "chat_template_kwargs":{"enable_thinking":False}}
        req=Request("http://127.0.0.1:8878/v1/chat/completions",
                    data=json.dumps(payload).encode(),
                    headers={"Content-Type":"application/json"},method="POST")
        with urlopen(req,timeout=39) as resp:
            if resp.status!=200: raise ValueError("Local inference unavailable")
            result=json.loads(resp.read(300000))
        text=str(result["choices"][0]["message"].get("content") or "").strip()
        print(json.dumps({"ok":bool(text),"text":text[:5000]},ensure_ascii=False))
        return 0 if text else 2
    except (ValueError,KeyError,IndexError,TypeError,URLError,HTTPError,OSError,TimeoutError):
        print(json.dumps({"ok":False,"error":"local_model_unavailable"}))
        return 2

if __name__=="__main__":
    raise SystemExit(main())
