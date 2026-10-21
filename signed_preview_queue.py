#!/usr/bin/env python3
"""Locally enrolled, signed, replay-safe PC *preview* deployment queue.

A trusted external attestor must independently verify real provider job logs,
exact checked-out commit, required checks and artifact before signing. This
module verifies its signed assertions; it does NOT contact GitHub/GitLab,
accept GitHub issue authorship as proof or grant production deployment.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import sys
import time

from hub import secure_write
import pc_releases as releases

SCHEMA = "open-ci-hub.signed-preview.v1"
POLICY_SCHEMA = "open-ci-hub.preview-trust.v1"
SHA = re.compile(r"^[a-f0-9]{40}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")
NONCE = re.compile(r"^[A-Za-z0-9_-]{22,80}$")
REPO = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+$")
STEP = re.compile(r"^[a-z][a-z0-9_.-]{0,50}$")
MAX_MANIFEST = 16_384
MAX_SIGNED_AGE = 86_400
MAX_FUTURE_SKEW = 30
MAX_QUEUED = 20
MIN_FREE_DISK = 4 * 1024 * 1024 * 1024


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _no_link_file(path, max_bytes):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise PermissionError("Untrusted or missing file.")
    if path.stat().st_size > max_bytes:
        raise PermissionError("Input file exceeds permitted size.")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > max_bytes:
            raise PermissionError("Input is not a bounded regular file.")
        out = bytearray()
        while len(out) <= max_bytes:
            block = os.read(fd, min(65536, max_bytes + 1 - len(out)))
            if not block:
                break
            out.extend(block)
        if len(out) > max_bytes:
            raise PermissionError("Input exceeded size budget.")
        return bytes(out)
    finally:
        os.close(fd)


def _policy_path(app):
    return releases._app_dir(app) / "queue-policy.json"


def _policy(app):
    data = releases._private_file(_policy_path(app))
    if not isinstance(data, dict) or set(data) != {
        "schema", "app", "repository", "issuer", "public_key_pem",
        "ref_prefixes", "required_checks", "preview_auto"
    } or data["schema"] != POLICY_SCHEMA or data["app"] != app:
        raise PermissionError("Invalid pinned preview-deployment policy.")
    if (not isinstance(data["repository"], str) or not REPO.fullmatch(data["repository"])
        or not isinstance(data["issuer"], str) or not data["issuer"].startswith("https://")
        or type(data["preview_auto"]) is not bool):
        raise PermissionError("Malformed signed-preview trust scope.")
    if not isinstance(data["public_key_pem"], str) or len(data["public_key_pem"]) > 2048:
        raise PermissionError("Invalid signer key.")
    if (not isinstance(data["ref_prefixes"], list) or not data["ref_prefixes"]
        or len(data["ref_prefixes"]) > 10
        or any(not isinstance(v, str) or not v.startswith("refs/heads/")
               or not v.endswith("/") or ".." in v or "//" in v
               or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-./"
                      for c in v) for v in data["ref_prefixes"])):
        raise PermissionError("Only explicit branch prefixes are permitted for auto previews.")
    if (not isinstance(data["required_checks"], list) or not data["required_checks"]
        or len(data["required_checks"]) > 30
        or len(set(data["required_checks"])) != len(data["required_checks"])
        or any(not isinstance(v, str) or not STEP.fullmatch(v)
               for v in data["required_checks"])):
        raise PermissionError("Missing trusted required-check allowlist.")
    return data


def pin(app, *, repository, issuer, public_key_path, ref_prefixes, required_checks,
        confirm=False):
    releases._owner()
    cfg = releases._config(app)
    if not confirm:
        raise PermissionError("Owner must locally confirm the exact release trust scope.")
    if not cfg["service"] or not cfg["health_port"]:
        raise PermissionError("Signed previews require an enrolled managed user service and health endpoint.")
    if (not isinstance(repository, str) or not REPO.fullmatch(repository)
        or not isinstance(issuer, str) or not issuer.startswith("https://")):
        raise ValueError("A specific repository and HTTPS attestation issuer are required.")
    target = _policy_path(app)
    if target.exists() or target.is_symlink():
        raise PermissionError("Signer already pinned; explicit audited rotation required.")
    pem = _no_link_file(public_key_path, 2048)
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    key = load_pem_public_key(pem)
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("Only Ed25519 public keys are supported.")
    data = {
        "schema": POLICY_SCHEMA, "app": app, "repository": repository,
        "issuer": issuer, "public_key_pem": pem.decode("ascii"),
        "ref_prefixes": ref_prefixes, "required_checks": required_checks,
        "preview_auto": False
    }
    # Validate before persisting (using a temporary private file isn't necessary
    # here: the user supplied fields are checked explicitly and on every use).
    if (not isinstance(ref_prefixes, list) or not 1 <= len(ref_prefixes) <= 10
        or any(not isinstance(x, str) or not x.startswith("refs/heads/")
               or not x.endswith("/") or ".." in x or "//" in x
               or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-./"
                      for c in x) for x in ref_prefixes)
        or not isinstance(required_checks, list) or not 1 <= len(required_checks) <= 30
        or len(set(required_checks)) != len(required_checks)
        or any(not isinstance(x, str) or not STEP.fullmatch(x) for x in required_checks)):
        raise ValueError("Invalid branch/check policy.")
    secure_write(target, json.dumps(data, sort_keys=True) + "\n")
    _policy(app)
    return {"app": app, "trust_pinned": True, "preview_auto": False}


def enable_preview(app, *, confirm=False):
    releases._owner()
    if not confirm:
        raise PermissionError("Preview standing consent must be explicitly enabled on this PC.")
    data = _policy(app)
    cfg = releases._config(app)
    if not cfg["service"] or not cfg["health_port"]:
        raise PermissionError("Unhealthy/non-service preview must not run unattended.")
    data["preview_auto"] = True
    secure_write(_policy_path(app), json.dumps(data, sort_keys=True) + "\n")
    return {"app": app, "preview_auto": True}


def verify_signed(app, manifest, *, now=None):
    """Require a locally pinned issuer-signed *complete* CI proof envelope."""
    trust = _policy(app)
    if not isinstance(manifest, dict) or set(manifest) != {"payload", "signature"}:
        raise PermissionError("Expected exact signed preview envelope.")
    obj = manifest["payload"]
    required = {
        "schema", "issuer", "app", "repository", "ref", "commit", "checked_out_sha",
        "artifact_sha256", "run_id", "run_status", "checks", "issued_at",
        "expires_at", "nonce", "environment"
    }
    if not isinstance(obj, dict) or set(obj) != required:
        raise PermissionError("Incomplete CI attestation payload.")
    clock = int(time.time() if now is None else now)
    if (obj["schema"] != SCHEMA or obj["app"] != app
        or obj["issuer"] != trust["issuer"]
        or obj["repository"] != trust["repository"]
        or obj["environment"] != "preview" or obj["run_status"] != "passed"
        or not isinstance(obj["commit"], str) or not SHA.fullmatch(obj["commit"])
        or obj["checked_out_sha"] != obj["commit"]
        or not isinstance(obj["artifact_sha256"], str)
        or not DIGEST.fullmatch(obj["artifact_sha256"])
        or not isinstance(obj["run_id"], str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", obj["run_id"])
        or not isinstance(obj["nonce"], str) or not NONCE.fullmatch(obj["nonce"])
        or type(obj["issued_at"]) is not int or type(obj["expires_at"]) is not int
        or obj["issued_at"] > clock + MAX_FUTURE_SKEW
        or obj["issued_at"] <= clock - MAX_SIGNED_AGE
        or obj["expires_at"] <= clock
        or obj["expires_at"] - obj["issued_at"] > MAX_SIGNED_AGE):
        raise PermissionError("Untrusted, stale or mismatched CI attestation.")
    ref = obj["ref"]
    if (not isinstance(ref, str)
        or not any(ref.startswith(prefix) and len(ref) > len(prefix)
                   for prefix in trust["ref_prefixes"])
        or ".." in ref or "//" in ref or any(x in ref for x in ("\x00", "\\", " ")))
        or len(ref) > 300):
        raise PermissionError("Source ref outside the enrolled preview scope.")
    checks = obj["checks"]
    if not isinstance(checks, dict) or set(checks) != set(trust["required_checks"]):
        raise PermissionError("CI evidence lacks mandatory checks.")
    for step in checks.values():
        if (not isinstance(step, dict)
            or set(step) != {"exit_code", "observed", "timed_out", "log_sha256"}
            or type(step["exit_code"]) is not int or step["exit_code"] != 0
            or step["observed"] is not True or step["timed_out"] is not False
            or not isinstance(step["log_sha256"], str)
            or not DIGEST.fullmatch(step["log_sha256"])):
            raise PermissionError("Missing observed successful CI check.")
    signature = manifest["signature"]
    if not isinstance(signature, str) or len(signature) > 128:
        raise PermissionError("Malformed signed receipt.")
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
        key = load_pem_public_key(trust["public_key_pem"].encode("ascii"))
        if not isinstance(key, Ed25519PublicKey):
            raise PermissionError("Wrong pinned signer type.")
        raw = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        if len(raw) != 64:
            raise PermissionError("Invalid signature length.")
        key.verify(raw, canonical(obj))
    except (ValueError, TypeError, InvalidSignature) as exc:
        raise PermissionError("Bad release attestation signature.") from exc
    return obj


@contextmanager
def _queue_lock(folder):
    path = folder / "queue.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        st = os.fstat(fd)
        if st.st_uid != os.geteuid() or st.st_mode & 0o077:
            raise PermissionError("Queue lock is not owner-private.")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _dirs(app):
    folder, _ = releases._path(app)
    state = folder / "queue"
    inbox = folder / "inbox"
    for p in (state, inbox):
        if p.is_symlink():
            raise PermissionError("Unsafe queue path.")
        p.mkdir(exist_ok=True, mode=0o700)
        st = p.stat()
        if st.st_uid != os.geteuid() or st.st_mode & 0o077:
            raise PermissionError("Queue directory is not private.")
    return folder, state, inbox


def _copy_blob(source_path, dest, expected_hash):
    source_path = Path(source_path)
    if source_path.is_symlink() or not source_path.is_file():
        raise PermissionError("Artifact must be an existing regular file.")
    if source_path.stat().st_size > releases.MAX_ARCHIVE_BYTES:
        raise PermissionError("Artifact too large.")
    h = hashlib.sha256()
    src = os.open(source_path, os.O_RDONLY | os.O_NOFOLLOW)
    out = None
    try:
        if not stat.S_ISREG(os.fstat(src).st_mode):
            raise PermissionError("Artifact is not a regular file.")
        out = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        size = 0
        while True:
            chunk = os.read(src, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > releases.MAX_ARCHIVE_BYTES:
                raise PermissionError("Artifact exceeds byte limit.")
            h.update(chunk)
            remaining = memoryview(chunk)
            while remaining:
                wrote = os.write(out, remaining)
                if wrote <= 0:
                    raise OSError("Incomplete artifact copy.")
                remaining = remaining[wrote:]
        os.fsync(out)
        if h.hexdigest() != expected_hash:
            raise PermissionError("Artifact SHA-256 does not match signed proof.")
    except BaseException:
        if out is not None:
            os.close(out)
            out = None
            os.unlink(dest)
        raise
    finally:
        os.close(src)
        if out is not None:
            os.close(out)


def submit(app, manifest_path, archive_path):
    releases._owner()
    if not _policy(app)["preview_auto"]:
        raise PermissionError("Owner has not enabled signed preview deployment.")
    raw = _no_link_file(manifest_path, MAX_MANIFEST)
    manifest = json.loads(raw)
    payload = verify_signed(app, manifest)
    folder, queue, inbox = _dirs(app)
    nonce = payload["nonce"]
    state_path = queue / (nonce + ".json")
    artifact_path = inbox / (nonce + ".zip")
    with _queue_lock(folder):
        if state_path.exists() or artifact_path.exists() or state_path.is_symlink():
            raise PermissionError("Duplicate or replayed signed deployment request.")
        active = sum(_record(p)["status"] in ("queued", "running")
                     for p in queue.glob("*.json"))
        if active >= MAX_QUEUED:
            raise PermissionError("Preview queue capacity exhausted.")
        artifact_bytes = Path(archive_path).stat().st_size
        if (shutil.disk_usage(folder).free < MIN_FREE_DISK + 4 * artifact_bytes):
            raise PermissionError("Insufficient reserve disk for staging and recovery.")
        _copy_blob(archive_path, artifact_path, payload["artifact_sha256"])
        try:
            record = {"status": "queued", "manifest": manifest,
                      "submitted_at": int(time.time()), "app": app}
            secure_write(state_path, json.dumps(record, sort_keys=True) + "\n")
        except BaseException:
            artifact_path.unlink(missing_ok=True)
            raise
    return {"app": app, "status": "queued", "nonce": nonce,
            "commit": payload["commit"], "run_id": payload["run_id"]}


def _record(path):
    result = releases._private_file(path)
    if (not isinstance(result, dict) or
        set(result) not in ({"status", "manifest", "submitted_at", "app"},
                            {"status", "manifest", "submitted_at", "app", "outcome"})
        or result["status"] not in ("queued", "running", "succeeded", "failed")):
        raise PermissionError("Invalid durable release record.")
    return result


def run_once(app):
    """Execute at most one queued preview; no automatic rerun after a crash."""
    releases._owner()
    if not _policy(app)["preview_auto"]:
        raise PermissionError("Preview standing consent is disabled.")
    cfg = releases._config(app)
    if not cfg["service"] or not cfg["health_port"]:
        raise PermissionError("A managed service and loopback health probe are mandatory.")
    folder, queue, inbox = _dirs(app)
    with _queue_lock(folder):
        for entry in sorted(queue.glob("*.json"), key=lambda p: p.name):
            record = _record(entry)
            if record["status"] != "queued":
                continue
            manifest = record["manifest"]
            try:
                payload = verify_signed(app, manifest)
                if payload["nonce"] != entry.stem or record["app"] != app:
                    raise PermissionError("Queue record identity mismatch.")
                source = inbox / (payload["nonce"] + ".zip")
                record["status"] = "running"
                secure_write(entry, json.dumps(record, sort_keys=True) + "\n")
                staged = releases.stage(app, archive=str(source),
                                        commit=payload["commit"],
                                        expected_sha256=payload["artifact_sha256"])
                deployed = releases.promote(app, payload["commit"], confirm=True)
                record["status"] = "succeeded"
                record["outcome"] = {"staged": staged["status"],
                                     "deployed": deployed["status"],
                                     "commit": payload["commit"],
                                     "archive_sha256": payload["artifact_sha256"]}
            except (OSError, ValueError, RuntimeError, PermissionError, TypeError) as exc:
                record["status"] = "failed"
                record["outcome"] = {"error": type(exc).__name__,
                                     "note": "Stopped without blind retry; inspect local logs."}
            secure_write(entry, json.dumps(record, sort_keys=True) + "\n")
            # The signed request remains journalled, preventing replay even
            # after deleting its copied artifact.
            if record["status"] == "succeeded":
                source.unlink(missing_ok=True)
            return {"app": app, "nonce": entry.stem, "status": record["status"],
                    "outcome": record.get("outcome")}
    return {"app": app, "status": "idle"}


def status(app):
    releases._owner()
    trust = _policy(app)
    _, queue, _ = _dirs(app)
    summary = {"queued": 0, "running": 0, "succeeded": 0, "failed": 0}
    for f in queue.glob("*.json"):
        state = _record(f)
        summary[state["status"]] += 1
    return {"app": app, "preview_auto": trust["preview_auto"],
            "repository": trust["repository"], "queue": summary}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("pin")
    p.add_argument("app")
    p.add_argument("--repository", required=True)
    p.add_argument("--issuer", required=True)
    p.add_argument("--public-key", required=True)
    p.add_argument("--ref-prefix", action="append", required=True)
    p.add_argument("--required-check", action="append", required=True)
    p.add_argument("--confirm", action="store_true")
    p = sub.add_parser("enable-preview")
    p.add_argument("app")
    p.add_argument("--confirm", action="store_true")
    p = sub.add_parser("submit")
    p.add_argument("app")
    p.add_argument("--manifest", required=True)
    p.add_argument("--archive", required=True)
    for action in ("status", "run-once"):
        p = sub.add_parser(action)
        p.add_argument("app")
    args = parser.parse_args(argv)
    try:
        if args.action == "pin":
            result = pin(args.app, repository=args.repository, issuer=args.issuer,
                         public_key_path=args.public_key, ref_prefixes=args.ref_prefix,
                         required_checks=args.required_check, confirm=args.confirm)
        elif args.action == "enable-preview":
            result = enable_preview(args.app, confirm=args.confirm)
        elif args.action == "submit":
            result = submit(args.app, args.manifest, args.archive)
        elif args.action == "run-once":
            result = run_once(args.app)
        else:
            result = status(args.app)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (PermissionError, OSError, ValueError, RuntimeError, TypeError) as exc:
        print("Signed preview rejected: " + str(exc)[:250], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
