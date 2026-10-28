#!/usr/bin/env python3
"""Explicit, local-only cloud AI provider connector for Open CI Hub.

No background requests, no automatic paid fallback, no secrets in Git, logs or URLs.
Keys are owner-provisioned through an interactive local terminal.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import getpass
import json
import os
from pathlib import Path
import re
import stat
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

@dataclass(frozen=True)
class Provider:
    name: str
    endpoint: str
    key_page: str
    model: str | None
    note: str

PROVIDERS = {
    "gemini": Provider("Google AI Studio", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                       "https://aistudio.google.com/apikey", "gemini-3.1-flash-lite",
                       "Free access is model/project dependent; content may be used to improve Google products."),
    "nvidia": Provider("NVIDIA NIM", "https://integrate.api.nvidia.com/v1/chat/completions",
                       "https://build.nvidia.com/", None,
                       "Trial/developer prototyping credits only; model and free status must be independently checked."),
    "openrouter": Provider("OpenRouter", "https://openrouter.ai/api/v1/chat/completions",
                           "https://openrouter.ai/settings/keys", "openrouter/free",
                           "Free models are limited; routing may change. No paid fallback is configured."),
    "groq": Provider("Groq Cloud", "https://api.groq.com/openai/v1/chat/completions",
                     "https://console.groq.com/keys", "openai/gpt-oss-20b",
                     "Free plan has model/org rate limits; verify account tier before sending."),
}

KEY_DIR = Path.home() / ".config" / "open-ci-hub" / "cloud-ai-keys"
_KEY_PATTERN = re.compile(r"^[!-~]{12,512}$")

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Refusing unexpected API redirect.")

def _secure_dir(*, create: bool) -> Path:
    parent = KEY_DIR.parent
    for part in (parent.parent, parent, KEY_DIR):
        if part.is_symlink():
            raise PermissionError("Symlinked credential directory refused.")
    if create:
        KEY_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        KEY_DIR.chmod(0o700)
    if not KEY_DIR.exists():
        return KEY_DIR
    s = KEY_DIR.stat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o700:
        raise PermissionError("Credential directory must be private (0700) and owned by you.")
    return KEY_DIR

def _key_path(provider: str) -> Path:
    if provider not in PROVIDERS:
        raise ValueError("Unknown provider.")
    return _secure_dir(create=False) / (provider + ".key")

def has_key(provider: str) -> bool:
    p = _key_path(provider)
    if not p.exists():
        return False
    s = p.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o600:
        raise PermissionError("API key file has unsafe permissions; refusing it.")
    return True

def _read_key(provider: str) -> str:
    if not has_key(provider):
        raise RuntimeError("No key configured: use 'add-key' in the owner's terminal.")
    path = _key_path(provider)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            key = stream.read(513).strip()
    except Exception:
        raise
    if not _KEY_PATTERN.fullmatch(key):
        raise RuntimeError("Stored API key format invalid.")
    return key

def add_key(provider: str) -> None:
    if not sys.stdin.isatty():
        raise PermissionError("Key entry requires a local interactive terminal. Never pass keys on the command line.")
    key = getpass.getpass(f"Paste the {PROVIDERS[provider].name} API key (hidden): ").strip()
    if not _KEY_PATTERN.fullmatch(key):
        raise ValueError("Key must be between 12 and 512 printable ASCII characters.")
    _secure_dir(create=True)
    dest = _key_path(provider)
    if dest.exists():
        raise FileExistsError("Key already exists; rotate it manually after revocation.")
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(key + "\n")
    finally:
        key = ""
    print(f"{provider}: key saved privately (0600); not tested or used yet.")

def catalog() -> list[dict]:
    return [{
        "id": ident,
        "name": p.name,
        "key_page": p.key_page,
        "model": p.model,
        "credential": "configured" if has_key(ident) else "not_configured",
        "auto_billing": False,
        "automatic_requests": False,
        "note": p.note,
    } for ident,p in PROVIDERS.items()]

def test_one(provider: str, *, confirm_free: bool) -> dict:
    if not confirm_free:
        raise PermissionError("Before a test, verify free tier in provider console and pass --confirm-free-account.")
    p = PROVIDERS[provider]
    if p.model is None:
        raise PermissionError("NVIDIA endpoint not enabled for calls until a specific free/trial model and credit terms are verified.")
    key = _read_key(provider)
    data = json.dumps({
        "model": p.model,
        "messages": [{"role":"user", "content":"Reply with exactly READY."}],
        "max_tokens": 32,
        "temperature": 0,
        "stream": False,
    }, separators=(",",":")).encode()
    request = Request(p.endpoint, data=data, headers={
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "OpenCIHub-OwnerManualTest/1.0",
    }, method="POST")
    # HTTPS only; no proxies, redirects or retries to other models/providers.
    if not p.endpoint.startswith("https://"):
        raise RuntimeError("HTTPS required.")
    client = build_opener(ProxyHandler({}), HTTPSHandler(), NoRedirect())
    try:
        with client.open(request, timeout=18) as resp:
            if resp.status != 200:
                raise RuntimeError("Provider rejected the request.")
            result = json.loads(resp.read(1024 * 512))
    except HTTPError as exc:
        return {"provider":provider, "status":"provider_error",
                "http_code":exc.code, "retry_performed":False}
    except (URLError, TimeoutError):
        return {"provider":provider, "status":"network_error", "retry_performed":False}
    choices = result.get("choices")
    if not isinstance(choices,list) or not choices:
        return {"provider":provider, "status":"bad_reply", "retry_performed":False}
    msg = choices[0].get("message",{})
    return {"provider":provider, "status":"completed",
            "model":p.model, "response":str(msg.get("content") or "")[:150],
            "retry_performed":False}

def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Show registered providers and key status, never key contents.")
    add=sub.add_parser("add-key", help="Interactive private key installation, owner machine only.")
    add.add_argument("provider", choices=PROVIDERS)
    test=sub.add_parser("test", help="One explicit low-token request, never paid fallback.")
    test.add_argument("provider", choices=PROVIDERS)
    test.add_argument("--confirm-free-account", action="store_true")
    args=parser.parse_args(argv)
    try:
        if args.command=="status":
            print(json.dumps(catalog(), indent=2))
        elif args.command=="add-key":
            add_key(args.provider)
        elif args.command=="test":
            print(json.dumps(test_one(args.provider, confirm_free=args.confirm_free_account)))
    except (ValueError, RuntimeError, PermissionError, FileExistsError, OSError) as e:
        print("Provider setup/test blocked: " + str(e), file=sys.stderr)
        return 2
    return 0

if __name__=="__main__":
    raise SystemExit(main())
