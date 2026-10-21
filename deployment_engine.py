#!/usr/bin/env python3
"""Policy-gated deployment orchestrator using existing signed Releases primitives.

No arbitrary project shell commands or provider tokens in manifests. Only local
pre-approved user services are executable in this increment; remote adapters
fail closed until separately implemented and tested.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from contextlib import contextmanager

from release_manager import Releases, APP, COMMIT, DIGEST

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".local" / "deployment-engine"
POLICY = STATE / "targets.json"
TARGET = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
ENVIRONMENTS = {"staging", "production"}
LOCAL_ADAPTER = "local-user-service"
DECLARED_ADAPTERS = {LOCAL_ADAPTER, "ssh-compose", "cloudflare-workers", "vercel"}
REQUIRED_GATES = ("tests", "build", "security", "database", "backup")
MAX_HISTORY_BYTES = 4_000_000


def private_dir(path: Path) -> Path:
    if path.is_symlink():
        raise RuntimeError("Deployment state must not be a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise RuntimeError("Deployment state must be a directory")
    os.chmod(path, 0o700)
    return path


def load_policy(path: Path = POLICY):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("Private target policy not installed")
    st = path.stat()
    if st.st_mode & 0o077:
        raise PermissionError("Target policy must be private (0600)")
    if st.st_size > 200_000:
        raise ValueError("Deployment target policy too large")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RuntimeError("Invalid deployment target policy") from None
    if not isinstance(raw, dict) or raw.get("schema") != 1:
        raise ValueError("Unknown deployment policy schema")
    targets = raw.get("targets")
    if not isinstance(targets, dict) or len(targets) > 128:
        raise ValueError("Expected a bounded target map")
    for key, cfg in targets.items():
        if not isinstance(key, str) or not TARGET.fullmatch(key) or not isinstance(cfg, dict):
            raise ValueError("Invalid target")
        if not isinstance(cfg.get("application"), str) or not APP.fullmatch(cfg["application"]):
            raise ValueError("Invalid application identifier")
        if cfg.get("environment") not in ENVIRONMENTS or cfg.get("adapter") not in DECLARED_ADAPTERS:
            raise ValueError("Unknown environment or adapter")
        if type(cfg.get("enabled", False)) is not bool:
            raise ValueError("Target enabled must be a boolean")
        if cfg.get("environment") == "production":
            staging = cfg.get("staging_target")
            if not isinstance(staging, str) or staging == key or staging not in targets:
                raise ValueError("Production target must name an existing staging target")
            sibling = targets[staging]
            if (sibling.get("environment") != "staging" or
                    sibling.get("application") != cfg["application"]):
                raise ValueError("Staging predecessor does not match production application")
        if any(k in cfg for k in ("secret", "token", "password", "private_key", "command", "argv")):
            raise ValueError("Secrets and executable commands are prohibited in policy")
    return targets


def signed_gates(releases: Releases, app: str, digest: str, sha: str):
    # This is a signed human-approved claim, not independent worker attestation.
    root = releases._root(app)
    inventory = releases._verify(root, digest)
    manifest_file = root / "releases" / digest / "release.json"
    obj = json.loads(manifest_file.read_text(encoding="utf-8"))
    releases._manifest(obj, app)
    if obj["source_commit"] != sha or inventory["source_commit"] != sha:
        raise RuntimeError("Signed release source commit does not match requested commit")
    checks = obj["checks"]
    for gate in REQUIRED_GATES:
        v = checks.get(gate)
        if not isinstance(v, dict) or v.get("status") != "passed" or not DIGEST.fullmatch(str(v.get("log_sha256", ""))):
            raise RuntimeError("Signed release gate not passed: " + gate)
    return {"source_commit":sha, "signed_bundle_sha256":digest,
            "signer":inventory.get("signer"),"checks":{k:checks[k] for k in REQUIRED_GATES},
            "attestation":"reviewer-signed metadata, not independent execution proof"}


@contextmanager
def locked_target(state: Path, target: str):
    lock_dir = private_dir(state / "locks")
    lock_file = lock_dir / (target + ".lock")
    if lock_file.is_symlink():
        raise RuntimeError("Symlinked deployment lock refused")
    fd = os.open(lock_file, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another deployment to this target is running") from None
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def history(state: Path, target: str):
    file = state / "history" / (target + ".jsonl")
    if file.is_symlink():
        raise RuntimeError("Symlinked deployment history refused")
    if not file.exists():
        return []
    if file.stat().st_size > MAX_HISTORY_BYTES:
        raise RuntimeError("Deployment history limit reached; archive securely before continuing")
    result = []
    for line in file.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if not isinstance(value, dict) or value.get("target") != target:
            raise RuntimeError("Corrupted deployment history")
        expected_previous = result[-1]["record_hash"] if result else "0" * 64
        got_hash = value.pop("record_hash", None)
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        if (value.get("previous_hash") != expected_previous or
                got_hash != hashlib.sha256(canonical).hexdigest()):
            raise RuntimeError("Deployment audit chain integrity failure")
        value["record_hash"] = got_hash
        result.append(value)
    return result


def record(state: Path, target: str, phase: str, sha: str, digest: str, detail: str = ""):
    folder = private_dir(state / "history")
    existing = history(state, target)
    prev_hash = existing[-1]["record_hash"] if existing else "0" * 64
    entry = {"when":dt.datetime.now(dt.timezone.utc).isoformat(),
             "target":target, "phase":phase, "sha":sha, "digest":digest,
             "detail":str(detail)[:350], "previous_hash":prev_hash}
    canon = json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()
    entry["record_hash"] = hashlib.sha256(canon).hexdigest()
    file = folder / (target + ".jsonl")
    fd = os.open(file, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return entry


class DeploymentEngine:
    def __init__(self, state: Path = STATE, policy: Path = POLICY, releases: Releases | None = None):
        self.state = private_dir(Path(state))
        self.policy = Path(policy)
        self.releases = releases or Releases()

    def target(self, name):
        configs = load_policy(self.policy)
        if name not in configs:
            raise PermissionError("Deployment target not allowlisted")
        cfg = configs[name]
        if cfg.get("enabled") is not True:
            raise PermissionError("Deployment target disabled in local policy")
        if cfg["environment"] == "production":
            # Current Releases policy is keyed only by application, so deploying
            # the same app through staging and production would share pointers.
            # Do not permit this until isolation and promotion are implemented.
            raise NotImplementedError("Production environment isolation is not implemented; no deployment occurred")
        if cfg["adapter"] != LOCAL_ADAPTER:
            raise NotImplementedError("Remote adapter not approved or implemented; no deployment occurred")
        return cfg

    def plan(self, target: str, sha: str, digest: str):
        cfg = self.target(target)
        if not isinstance(sha, str) or not COMMIT.fullmatch(sha) or not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise ValueError("Exact 40-character source SHA and 64-character archive digest required")
        evidence = signed_gates(self.releases, cfg["application"], digest, sha)
        prod_ready = None
        if cfg["environment"] == "production":
            previous = history(self.state, cfg["staging_target"])
            prod_ready = any(
                item.get("phase") == "healthy" and item.get("sha") == sha and
                item.get("digest") == digest for item in previous
            )
            if not prod_ready:
                raise PermissionError("Matching SHA and signed digest have not passed verified staging")
        return {"target":target, "application":cfg["application"],
                "environment":cfg["environment"], "adapter":cfg["adapter"],
                "source_commit":sha, "release_digest":digest,
                "signed_evidence":evidence, "matching_staging_verified":prod_ready,
                "auto_production_enabled":False}

    def deploy(self, target: str, sha: str, digest: str, *, confirm=False):
        if confirm is not True:
            raise PermissionError("Explicit operator confirmation required (no auto-production)")
        with locked_target(self.state,target):
            plan=self.plan(target,sha,digest)
            app=plan["application"]
            previous=self.releases.status(app)["current"]
            record(self.state,target,"requested",sha,digest,"operator-confirmed")
            try:
                result=self.releases.deploy(app,digest,confirm=True)
                if result["state"] not in ("healthy","already-active"):
                    raise RuntimeError("Unexpected release-manager result")
                record(self.state,target,"healthy",sha,digest,"local service health verified by release manager")
                return {**plan, "status":"healthy", "previous_release":previous,
                        "release_result":result}
            except Exception as exc:
                # Releases.deploy itself performs pointer restoration and local
                # service restart. Preserve the uncertainty if recovery failed.
                record(self.state,target,"failed",sha,digest,type(exc).__name__+": "+str(exc))
                raise

    def status(self, target: str):
        cfg=self.target(target)
        return {"target":target, "release":self.releases.status(cfg["application"]),
                "recent_history":history(self.state,target)[-20:]}


def main(argv=None):
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--policy",type=Path,default=POLICY)
    sub=cli.add_subparsers(dest="action",required=True)
    for action in ("plan","deploy","status"):
        p=sub.add_parser(action);p.add_argument("target")
        if action!="status":
            p.add_argument("commit");p.add_argument("digest")
        if action=="deploy":
            p.add_argument("--confirm",action="store_true")
    args=cli.parse_args(argv)
    try:
        engine=DeploymentEngine(policy=args.policy)
        if args.action=="plan":
            result=engine.plan(args.target,args.commit,args.digest)
        elif args.action=="deploy":
            result=engine.deploy(args.target,args.commit,args.digest,confirm=args.confirm)
        else:
            result=engine.status(args.target)
        print(json.dumps(result,indent=2))
        return 0
    except (RuntimeError,ValueError,PermissionError,OSError,NotImplementedError,KeyError) as exc:
        print("Open CI Hub deployment:",str(exc)[:380],file=sys.stderr)
        return 1
if __name__=="__main__":
    raise SystemExit(main())
