"""Standing owner consent for automatic reuse of an already encrypted site session.

Consent is per exact HTTPS hostname and restricted to approved read-only browser
missions. It is authenticated by HMAC derived from the desktop keyring key.
The agent never receives a password, vault key, or authority to submit changes.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import urlsplit

from browser_session_vault import HOME_DIR, _folder, _owned_file, secret_key, load
from autonomy_core import canonical, valid_host

MAX_DAYS = 90
SCOPE = "read_only_browser"
SCHEMA = 1


def grant_path(host, *, folder=HOME_DIR):
    valid_host(host)
    return _folder(folder) / (host + ".autouse.json")


def _sign(record, key):
    if type(key) is not bytes or len(key) != 32:
        raise ValueError("Invalid protected grant key.")
    return hmac.new(key, canonical(record).encode("utf8"),
                    hashlib.sha256).hexdigest()


def authorise(host, *, days=90, confirm=False, folder=HOME_DIR,
              key=None, now=None, verify_session=True):
    if confirm is not True:
        raise PermissionError("Standing browser-session reuse requires owner approval.")
    if type(days) is not int or not 1 <= days <= MAX_DAYS:
        raise ValueError("Standing grant must expire within 90 days.")
    now = int(time.time()) if now is None else int(now)
    key = secret_key() if key is None else key
    if verify_session:
        # A standing permission is never a login and cannot conjure credentials.
        load(host, key=key, folder=folder, now=now)
    path = grant_path(host, folder=folder)
    if path.is_symlink() or path.exists():
        raise FileExistsError("Grant already exists; revoke it before replacement.")
    grant = {"version": SCHEMA, "host": host, "scope": SCOPE,
             "issued": now, "expires": now + days * 86400}
    record = {"grant": grant, "mac": _sign(grant, key)}
    encoded = canonical(record).encode()
    fd, temp = tempfile.mkstemp(prefix=".grant-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
        os.link(temp, path)  # atomic no-overwrite
    finally:
        Path(temp).unlink(missing_ok=True)
    return {"site": host, "scope": SCOPE, "auto_reuse": True,
            "approval_expires": grant["expires"]}


def enabled(host, *, folder=HOME_DIR, key=None, now=None):
    """False means no grant; corrupt/expired consent is never treated as approval."""
    valid_host(host)
    root = Path(folder)
    if not root.exists() and not root.is_symlink():
        return False
    root = _folder(folder)  # reject symlink/weakly permissioned storage
    path = root / (host + ".autouse.json")
    if not path.exists() and not path.is_symlink():
        return False
    raw = _owned_file(path).read_bytes()
    if len(raw) > 4096:
        raise PermissionError("Invalid standing login grant.")
    try:
        record = json.loads(raw)
        if not isinstance(record, dict) or set(record) != {"grant", "mac"}:
            raise ValueError("Invalid record fields.")
        grant = record["grant"]
        now = int(time.time()) if now is None else int(now)
        if (not isinstance(grant, dict) or set(grant) !=
            {"version", "host", "scope", "issued", "expires"}
            or grant["version"] != SCHEMA or grant["host"] != host
            or grant["scope"] != SCOPE or type(grant["issued"]) is not int
            or type(grant["expires"]) is not int
            or grant["issued"] > now or grant["expires"] <= now
            or grant["expires"] - grant["issued"] > MAX_DAYS * 86400):
            raise ValueError("Grant expired or not scoped correctly.")
        key = secret_key() if key is None else key
        if not hmac.compare_digest(record["mac"], _sign(grant, key)):
            raise ValueError("Grant integrity failure.")
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise PermissionError("Stored automatic-login permission is invalid.") from None
    return True


def select_host(plan, step, *, folder=HOME_DIR, key=None, now=None):
    """Only one exact site, HTTPS, no interaction, no transfers, no task mutation."""
    if not isinstance(plan, dict) or not isinstance(step, dict):
        return None
    if step.get("kind") != "browse":
        return None
    hosts = plan.get("hosts")
    if not isinstance(hosts, list) or len(hosts) != 1:
        return None
    host = hosts[0]
    if not isinstance(host, str):
        return None
    valid_host(host)
    source = step.get("input")
    if not isinstance(source, dict):
        return None
    try:
        url = urlsplit(source.get("url", ""))
    except (TypeError, ValueError):
        return None
    if (url.scheme != "https" or url.hostname != host or
        plan.get("limits", {}).get("allow_browser_interaction") is not False or
        not isinstance(source.get("actions"), list) or
        not source["actions"] or any(
            not isinstance(a, dict) or a.get("type") not in ("extract", "screenshot")
            for a in source["actions"])):
        return None
    return host if enabled(host, folder=folder, key=key, now=now) else None


def revoke(host, *, folder=HOME_DIR, confirm=False):
    if confirm is not True:
        raise PermissionError("Revoking automatic access requires explicit approval.")
    path = grant_path(host, folder=folder)
    if path.is_symlink():
        raise PermissionError("Symlinked approval file rejected.")
    path.unlink(missing_ok=True)
    return {"site": host, "auto_reuse": False, "vault_session_retained": True}


def list_grants(*, folder=HOME_DIR, key=None, now=None):
    root = Path(folder)
    if not root.exists():
        return []
    results = []
    for path in sorted(root.glob("*.autouse.json"))[:128]:
        host = path.name[:-len(".autouse.json")]
        try:
            if enabled(host, folder=folder, key=key, now=now):
                results.append({"site": host, "auto_reuse": True,
                                "scope": SCOPE})
        except (OSError, ValueError, PermissionError):
            # Do not expose any private cookie or grant contents.
            continue
    return results
