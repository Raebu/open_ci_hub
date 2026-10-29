#!/usr/bin/env python3
"""Local-only RaeburnAI 2B request bridge; JSON via private Tailscale SSH stdin.

No public listener, user-provided URL, arbitrary model choice or cloud calls.
"""
import json
import sys
from urllib.request import Request,urlopen
from urllib.error import URLError,HTTPError

MODEL="RaeburnAI-Qwen3.5-2B-Q4_K_M.gguf"

def main():
    try:
        payload=sys.stdin.buffer.read(15000)
        if len(payload)>14000: raise ValueError("too_large")
        data=json.loads(payload)
        if not isinstance(data,dict) or data.get("model")!=MODEL:
            raise ValueError("wrong_model")
        messages=data.get("messages")
        if (not isinstance(messages,list) or len(messages)!=1
            or not isinstance(messages[0],dict)
            or messages[0].get("role")!="user"
            or not isinstance(messages[0].get("content"),str)
            or len(messages[0]["content"])>4200):
            raise ValueError("bad_message")
        size=data.get("max_tokens",0)
        if type(size) is not int or not 1<=size<=256:
            raise ValueError("bad_budget")
        request={"model":MODEL,"messages":messages,"stream":False,
                 "max_tokens":size,"temperature":0,
                 "chat_template_kwargs":{"enable_thinking":False}}
        req=Request("http://127.0.0.1:8879/v1/chat/completions",
                    data=json.dumps(request).encode(),
                    headers={"Content-Type":"application/json"},method="POST")
        with urlopen(req,timeout=65) as r:
            if r.status!=200: raise ValueError("model_not_available")
            result=json.loads(r.read(350000))
        text=str(result["choices"][0]["message"].get("content") or "").strip()
        print(json.dumps({"ok":bool(text),"text":text[:4000]},ensure_ascii=False))
        return 0 if text else 2
    except (ValueError,IndexError,KeyError,TypeError,OSError,URLError,HTTPError,TimeoutError):
        print(json.dumps({"ok":False,"error":"raeburnai_unavailable"}))
        return 2

if __name__=="__main__":
    raise SystemExit(main())
