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
from deployment_providers import CredentialStore, CloudflareVersions, VercelAlias, SSHCompose, https_health

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
    # A single production and staging target must never share a live resource.
    resources=set()
    for cfg in targets.values():
        if cfg.get("enabled") is not True or cfg["adapter"] not in ("cloudflare-workers","vercel","ssh-compose"):
            continue
        resource=(cfg["adapter"],cfg.get("account_id",""),cfg.get("script",""),
                  cfg.get("alias",""),cfg.get("host",""),cfg.get("project",""))
        if resource in resources:
            raise ValueError("Two enabled deployment targets share the same remote resource")
        resources.add(resource)
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
        if cfg["environment"] == "production" and cfg["adapter"] == LOCAL_ADAPTER:
            # Local signed release pointers are keyed by app and could alias staging.
            raise NotImplementedError("Local production environment isolation unavailable")
        if cfg["adapter"] not in DECLARED_ADAPTERS:
            raise NotImplementedError("Unknown adapter")
        return cfg

    def provider_metadata(self, target: str, cfg: dict, digest: str):
        # Provider reference must be inside the signed archive inventory.
        root=self.releases._root(cfg["application"])
        signed=self.releases._verify(root,digest)
        if "provider.json" not in signed["files"]:
            raise PermissionError("Immutable signed provider release metadata missing")
        meta=json.loads((root/"releases"/digest/"provider.json").read_text())
        if not isinstance(meta,dict) or meta.get("schema")!=1:
            raise ValueError("Invalid signed provider release metadata")
        item=meta.get("targets",{}).get(target)
        if not isinstance(item,dict) or item.get("adapter")!=cfg["adapter"]:
            raise PermissionError("Signed artifact not approved for this exact target")
        ident=item.get("version")
        if not isinstance(ident,str) or len(ident)>120:
            raise ValueError("Missing immutable provider version")
        return ident

    def _remote_adapter(self,cfg):
        creds=CredentialStore(self.state/"credentials")
        if cfg["adapter"]=="cloudflare-workers":
            return CloudflareVersions(cfg,creds)
        if cfg["adapter"]=="vercel":
            return VercelAlias(cfg,creds)
        if cfg["adapter"]=="ssh-compose":
            return SSHCompose(cfg,creds)
        raise ValueError("No remote adapter")

    def _deploy_remote(self,cfg,target,plan):
        version=plan["provider_version"]
        adapter=self._remote_adapter(cfg)
        if cfg["adapter"]=="ssh-compose":
            # First staging adapter supports only a preinstalled reviewed
            # immutable Compose file. Never deploy unknown remote volumes,
            # unpinned images, or missing recovery instructions.
            if cfg["environment"]=="production":
                raise NotImplementedError("SSH production requires independently verified remote rollback")
            if cfg.get("no_mutable_volumes_verified") is not True or cfg.get("rollback_plan_verified") is not True:
                raise PermissionError("Remote volume and rollback plan not authorised")
            images=adapter.execute("images")["images"]
            required=plan["signed_provider_images"]
            if not required or sorted(images)!=sorted(required):
                raise PermissionError("Remote Compose images differ from signed digest-pinned release")
            adapter.execute("config")
            adapter.execute("pull")
            adapter.execute("up")
            self._health_target(cfg)
            return {"provider_deployment_id":version,"rollback":"requires-reviewed-compose-recovery"}
        old=adapter.current()  # fail closed if rollback anchor unavailable
        if old==version:
            self._health_target(cfg)
            return {"provider_deployment_id":version,"previous":old,"unchanged":True}
        changed=False
        try:
            result=adapter.promote(version)
            changed=True
            self._health_target(cfg)
            return {**result,"previous":old,"rollback_ready":True}
        except Exception as exc:
            if not changed:
                # A network exception can happen after provider mutation;
                # never claim that a rollback was unnecessary or successful.
                raise RuntimeError("Provider promotion uncertain; check the target manually") from None
            try:
                adapter.rollback(old)
                self._health_target(cfg)
                raise RuntimeError("New release unhealthy; previous version restored") from exc
            except RuntimeError as rollback_err:
                if "previous version restored" in str(rollback_err):
                    raise
                raise RuntimeError("DEPLOYMENT INCIDENT: rollback failed; operator required") from None

    @staticmethod
    def _health_target(cfg):
        from urllib.parse import urlsplit
        url=cfg.get("health_url")
        if not isinstance(url,str):
            raise RuntimeError("An approved HTTPS health URL is required")
        host=urlsplit(url).hostname
        if not host or cfg.get("health_host")!=host:
            raise PermissionError("Unapproved health hostname")
        https_health(url,allowed_host=host)

    def plan(self, target: str, sha: str, digest: str):
        cfg = self.target(target)
        if not isinstance(sha, str) or not COMMIT.fullmatch(sha) or not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise ValueError("Exact 40-character source SHA and 64-character archive digest required")
        evidence = signed_gates(self.releases, cfg["application"], digest, sha)
        prod_ready = None
        if cfg["environment"] == "production":
            previous = history(self.state, cfg["staging_target"])
            # A failed or superseded staging release is not permission to promote.
            prod_ready = bool(previous and previous[-1].get("phase") == "healthy" and
                              previous[-1].get("sha") == sha and
                              previous[-1].get("digest") == digest)
            if not prod_ready:
                raise PermissionError("Matching SHA and signed digest have not passed verified staging")
        provider_version=None
        signed_images=[]
        if cfg["adapter"]!=LOCAL_ADAPTER:
            provider_version=self.provider_metadata(target,cfg,digest)
            self._health_target_config(cfg)
            if cfg["adapter"]=="ssh-compose":
                root=self.releases._root(cfg["application"])
                meta=json.loads((root/"releases"/digest/"provider.json").read_text())
                signed_images=meta["targets"][target].get("images",[])
                import re
                if (not isinstance(signed_images,list) or not signed_images or
                    any(not isinstance(i,str) or not re.fullmatch(
                      r"[a-zA-Z0-9][a-zA-Z0-9._:/@-]{4,240}@sha256:[0-9a-f]{64}",i)
                        for i in signed_images)):
                    raise ValueError("SSH release requires signed digest-pinned image list")
        return {"target":target, "application":cfg["application"],
                "environment":cfg["environment"], "adapter":cfg["adapter"],
                "source_commit":sha, "release_digest":digest,
                "signed_evidence":evidence, "matching_staging_verified":prod_ready,
                "provider_version":provider_version, "signed_provider_images":signed_images,
                "auto_production_enabled":cfg["environment"]=="production" and
                  cfg.get("automatic") is True and cfg.get("approved_policy") is True}

    @staticmethod
    def _health_target_config(cfg):
        from urllib.parse import urlsplit
        url=cfg.get("health_url")
        u=urlsplit(url) if isinstance(url,str) else None
        if (not u or u.scheme!="https" or not u.hostname or u.hostname!=cfg.get("health_host")
                or u.username or u.password or u.port not in (None,443)):
            raise ValueError("Approved HTTPS health check required")

    def deploy(self, target: str, sha: str, digest: str, *, confirm=False, automatic=False):
        if confirm is not True and automatic is not True:
            raise PermissionError("Explicit operator confirmation or approved automation required")
        if confirm is True and automatic is True:
            raise ValueError("Choose one approval route")
        if automatic:
            # A private manifest flag alone is not independent CI attestation.
            # Do not start automated production until a verifiable CI release
            # receipt and separate approval policy mechanism are integrated.
            raise NotImplementedError("Automatic production gated: independent CI attestation and owner approval service not yet verified")
        with locked_target(self.state,target):
            plan=self.plan(target,sha,digest)
            if automatic and not plan["auto_production_enabled"]:
                raise PermissionError("Automatic release forbidden by signed gates or owner policy")
            app=plan["application"]
            previous=(self.releases.status(app)["current"] if plan["adapter"]==LOCAL_ADAPTER
                      else (history(self.state,target)[-1]["digest"] if history(self.state,target) else None))
            record(self.state,target,"requested",sha,digest,"operator-confirmed")
            try:
                if plan["adapter"]==LOCAL_ADAPTER:
                    result=self.releases.deploy(app,digest,confirm=True)
                    if result["state"] not in ("healthy","already-active"):
                        raise RuntimeError("Unexpected release-manager result")
                else:
                    result=self._deploy_remote(self.target(target),target,plan)
                record(self.state,target,"healthy",sha,digest,"health verified; version="+str(result.get("provider_deployment_id","local-service"))[:95])
                return {**plan, "status":"healthy", "previous_release":previous,
                        "release_result":result}
            except Exception as exc:
                # Releases.deploy itself performs pointer restoration and local
                # service restart. Preserve the uncertainty if recovery failed.
                record(self.state,target,"failed",sha,digest,type(exc).__name__+": "+str(exc))
                raise

    def status(self, target: str):
        cfg=self.target(target)
        return {"target":target,"environment":cfg["environment"],"adapter":cfg["adapter"],
                "release":(self.releases.status(cfg["application"]) if cfg["adapter"]==LOCAL_ADAPTER else None),
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
