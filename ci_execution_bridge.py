#!/usr/bin/env python3
"""Open CI Hub's local-first, provider-neutral execution bridge.

Provider events are authenticated and deduplicated before entering a durable
SQLite queue. Repository code runs only in a bounded container with no host
credentials or Docker socket. The bridge is intentionally not a GitHub Actions
or GitLab cloud runner replacement; it owns the local job lifecycle and reports
provider commit statuses through the authenticated local provider CLIs.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fnmatch
import hashlib
import hmac
import html
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import tomllib
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote, urlsplit
from urllib.request import urlopen

from operations import invoke, invoke_json, safe_repo

ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / ".local"
CONFIG_PATH = LOCAL / "ci-bridge-config.json"
SECRETS_PATH = LOCAL / "ci-bridge-secrets.json"
DB_PATH = LOCAL / "ci-execution.sqlite3"
WORK_ROOT = LOCAL / "ci-bridge-work"
ARTIFACT_ROOT = LOCAL / "ci-bridge-artifacts"
LOG_ROOT = LOCAL / "ci-bridge-logs"
CACHE_ROOT = LOCAL / "ci-bridge-cache"
MAX_CACHE_BYTES = 2 * 1024 * 1024 * 1024
MAX_RELEASE_BUNDLE_BYTES = 30 * 1024 * 1024
MAX_RELEASE_SIGNATURE_BYTES = 10_000
DEPLOY_APP_RE = re.compile(r"^[a-z][a-z0-9-]{1,50}$")
SIGNER_RE = re.compile(r"^[A-Za-z0-9._@+-]{3,150}$")
SCHEMA = "open-ci-hub.ci-bridge.v1"
SECRETS_SCHEMA = "open-ci-hub.ci-bridge-secrets.v1"
WORKFLOW_FILE = ".open-ci-hub.toml"
SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
JOB_RE = re.compile(r"^[0-9a-f]{32}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/*-]{1,160}$")
STEP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,79}$")
IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,180}$")
DELIVERY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
MAX_BODY = 256 * 1024
MAX_PENDING = 100
MAX_JOBS = 10000
MAX_STEPS = 20
MAX_STEP_SECONDS = 900
MAX_JOB_SECONDS = 1800
MAX_OUTPUT_BYTES = 512 * 1024
MAX_ARTIFACT_BYTES = 50 * 1024 * 1024
MAX_ARTIFACT_FILES = 1000
MAX_ATTEMPTS = 3
RESOURCE_RETRY_SECONDS = 60
DEFAULT_IMAGES = ["python:3.12-slim", "node:22-bookworm-slim", "ubuntu:24.04", "alpine:3.20"]
ACTIVE_PROCESSES: dict[str, subprocess.Popen] = {}
ACTIVE_LOCK = threading.Lock()
STOP = threading.Event()


class ResourceDeferred(RuntimeError):
    """Transient host pressure: retain the job and retry after a cooldown."""


def now() -> int:
    return int(time.time())


def utc_iso(timestamp: int | None = None) -> str:
    return datetime.fromtimestamp(timestamp or now(), timezone.utc).isoformat()


def require_owner() -> None:
    if os.name != "posix" or os.geteuid() == 0:
        raise PermissionError("CI execution bridge commands must run as the ordinary local user, never root.")


def _private_dir(path: Path) -> None:
    if path.is_symlink():
        raise PermissionError("Refusing symlinked CI bridge state.")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.geteuid():
        raise PermissionError("CI bridge state must be owned by the desktop user.")
    os.chmod(path, 0o700)


def _check_private_file(path: Path, *, required: bool = False) -> bool:
    if path.is_symlink() or path.parent.is_symlink():
        raise PermissionError("Refusing symlinked CI bridge state.")
    if not path.exists():
        if required:
            raise RuntimeError(f"CI bridge is not initialised: {path.name} is missing.")
        return False
    st = path.stat()
    if not path.is_file() or st.st_uid != os.geteuid() or (st.st_mode & 0o077):
        raise PermissionError(f"{path.name} must be a regular owner-owned file with mode 0600.")
    return True


def _write_private_json(path: Path, data: dict) -> None:
    _private_dir(path.parent)
    if path.is_symlink():
        raise PermissionError("Refusing to replace a symlinked CI bridge file.")
    fd, temporary = tempfile.mkstemp(prefix=".ci-bridge-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def default_config() -> dict:
    return {
        "schema": SCHEMA,
        "max_concurrent": 1,
        "min_free_ram_mb": 3072,
        "min_free_disk_gb": 20,
        "max_job_seconds": MAX_JOB_SECONDS,
        "poll_seconds": 60,
        "public_job_base_url": "",
        "repos": [],
    }


def init_local(*, confirm: bool = False) -> dict:
    require_owner()
    if confirm is not True:
        raise PermissionError("Local owner confirmation is required: init --confirm.")
    if CONFIG_PATH.exists() or SECRETS_PATH.exists():
        raise FileExistsError("CI bridge configuration already exists; refusing to overwrite it.")
    _private_dir(LOCAL)
    owner_token = secrets.token_urlsafe(36)
    _write_private_json(CONFIG_PATH, default_config())
    _write_private_json(SECRETS_PATH, {
        "schema": SECRETS_SCHEMA,
        "owner_token": owner_token,
        "github_webhook_secret": "",
        "gitlab_webhook_secret": "",
    })
    return {
        "initialised": True,
        "config": str(CONFIG_PATH.relative_to(ROOT)),
        "secrets": str(SECRETS_PATH.relative_to(ROOT)),
        "owner_token": owner_token,
        "note": "Store the owner token privately now; it is shown once and never logged. Provider webhook secrets are configured separately.",
    }


def read_config() -> dict:
    _check_private_file(CONFIG_PATH, required=True)
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RuntimeError("CI bridge configuration is unreadable; it was not changed.") from None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise RuntimeError("Unsupported CI bridge configuration schema.")
    if type(data.get("max_concurrent")) is not int or not 1 <= data["max_concurrent"] <= 3:
        raise RuntimeError("max_concurrent must be 1–3.")
    if type(data.get("min_free_ram_mb")) is not int or not 2048 <= data["min_free_ram_mb"] <= 16384:
        raise RuntimeError("min_free_ram_mb must be 2048–16384.")
    if type(data.get("min_free_disk_gb")) is not int or not 5 <= data["min_free_disk_gb"] <= 500:
        raise RuntimeError("min_free_disk_gb must be 5–500.")
    if type(data.get("max_job_seconds")) is not int or not 60 <= data["max_job_seconds"] <= MAX_JOB_SECONDS:
        raise RuntimeError("max_job_seconds must be 60–1800.")
    if type(data.get("poll_seconds")) is not int or not 15 <= data["poll_seconds"] <= 3600:
        raise RuntimeError("poll_seconds must be 15–3600.")
    if not isinstance(data.get("repos"), list) or len(data["repos"]) > 500:
        raise RuntimeError("Invalid repository allowlist.")
    seen = set()
    for item in data["repos"]:
        if not isinstance(item, dict) or item.get("provider") not in ("github", "gitlab"):
            raise RuntimeError("Invalid repository allowlist entry.")
        repo = safe_repo(item.get("repo", ""))
        key = (item["provider"], repo.lower())
        if key in seen:
            raise RuntimeError("Duplicate provider/repository allowlist entry.")
        seen.add(key)
        branches = item.get("branches")
        images = item.get("allowed_images")
        if (not isinstance(branches, list) or not branches or len(branches) > 30
                or any(not isinstance(b, str) or not BRANCH_RE.fullmatch(b) or ".." in b for b in branches)):
            raise RuntimeError(f"Invalid branch allowlist for {repo}.")
        if type(item.get("allow_pull_requests")) is not bool or type(item.get("allow_network")) is not bool:
            raise RuntimeError(f"Invalid pull-request/network policy for {repo}.")
        if type(item.get("require_protected_branch")) is not bool:
            raise RuntimeError(f"Invalid protected-branch policy for {repo}.")
        if (not isinstance(images, list) or not images or len(images) > 20
                or any(not isinstance(img, str) or not IMAGE_RE.fullmatch(img) for img in images)):
            raise RuntimeError(f"Invalid approved container images for {repo}.")
    public_url = data.get("public_job_base_url", "")
    if not isinstance(public_url,str):
        raise RuntimeError("public_job_base_url must be a string.")
    if public_url:
        parsed = urlsplit(public_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise RuntimeError("public_job_base_url must be an HTTPS URL without credentials, query or fragment.")
    return data


def read_secrets() -> dict:
    _check_private_file(SECRETS_PATH, required=True)
    try:
        data = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RuntimeError("CI bridge secrets are unreadable; they were not changed.") from None
    if not isinstance(data, dict) or data.get("schema") != SECRETS_SCHEMA:
        raise RuntimeError("Unsupported CI bridge secrets schema.")
    if not isinstance(data.get("owner_token"), str) or len(data["owner_token"]) < 40:
        raise RuntimeError("Owner token is missing or invalid.")
    for key in ("github_webhook_secret", "gitlab_webhook_secret"):
        if not isinstance(data.get(key, ""), str) or (data.get(key) and len(data[key]) < 32):
            raise RuntimeError(f"{key} must be empty or at least 32 characters.")
    return data


def _branch_allowed(repo_cfg: dict, branch: str) -> bool:
    return isinstance(branch, str) and bool(branch) and any(fnmatch.fnmatchcase(branch, pattern) for pattern in repo_cfg["branches"])


def _repo_config(provider: str, repo: str, config: dict | None = None) -> dict:
    repo = safe_repo(repo)
    data = config or read_config()
    for item in data["repos"]:
        if item["provider"] == provider and item["repo"].lower() == repo.lower():
            return item
    raise PermissionError("Repository is not on the local CI execution allowlist.")


def add_repo(provider: str, repo: str, branches: list[str], *, allow_pull_requests: bool = False,
             allow_network: bool = False, allow_unprotected: bool = False,
             allowed_images: list[str] | None = None, confirm: bool = False) -> dict:
    require_owner()
    if confirm is not True:
        raise PermissionError("Local owner confirmation is required: allow-repo --confirm.")
    if provider not in ("github", "gitlab"):
        raise ValueError("Provider must be github or gitlab.")
    repo = safe_repo(repo)
    if not isinstance(branches, list) or not branches or len(branches) > 30:
        raise ValueError("Supply one or more explicit branch names/patterns.")
    if any(not isinstance(b, str) or not BRANCH_RE.fullmatch(b) or ".." in b for b in branches):
        raise ValueError("Invalid branch pattern.")
    config = read_config()
    existing = next((x for x in config["repos"] if x["provider"] == provider and x["repo"].lower() == repo.lower()), None)
    images = allowed_images if allowed_images is not None else ["python:3.12-slim"]
    if not isinstance(images,list) or not images or len(images)>20 or any(not isinstance(img,str) or not IMAGE_RE.fullmatch(img) for img in images):
        raise ValueError("Supply one or more valid, explicitly approved container image names.")
    entry = {
        "provider": provider,
        "repo": repo,
        "branches": list(dict.fromkeys(branches)),
        "allow_pull_requests": bool(allow_pull_requests),
        "allow_network": bool(allow_network),
        "require_protected_branch": not allow_unprotected,
        "allowed_images": list(dict.fromkeys(images)),
    }
    if existing:
        config["repos"][config["repos"].index(existing)] = entry
    else:
        if len(config["repos"]) >= 500:
            raise ValueError("Repository allowlist is full.")
        config["repos"].append(entry)
    _write_private_json(CONFIG_PATH, config)
    return {"result": "allowed", "provider": provider, "repo": repo, "branches": entry["branches"],
            "allow_pull_requests": entry["allow_pull_requests"], "allow_network": entry["allow_network"],
            "require_protected_branch": entry["require_protected_branch"],
            "note": "Only branches listed here are eligible. Workflow image tags must already exist locally; the bridge never pulls images automatically."}


def set_webhook_secret(provider: str, secret: str, *, confirm: bool = False) -> dict:
    require_owner()
    if confirm is not True:
        raise PermissionError("Local owner confirmation is required.")
    if provider not in ("github", "gitlab") or not isinstance(secret, str) or len(secret) < 32 or "\n" in secret or "\r" in secret:
        raise ValueError("Supply a GitHub/GitLab webhook secret of at least 32 characters.")
    data = read_secrets()
    key = "github_webhook_secret" if provider == "github" else "gitlab_webhook_secret"
    data[key] = secret
    _write_private_json(SECRETS_PATH, data)
    return {"result": "saved", "provider": provider, "secret_length": len(secret), "secret_value_returned": False}


class Queue:
    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path)
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise PermissionError("CI execution database cannot use symlinked paths.")
        if self.path.exists():
            _check_private_file(self.path, required=True)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.parent.stat().st_uid != os.geteuid():
            raise PermissionError("CI execution database directory must be owned by the desktop user.")
        os.chmod(self.path.parent, 0o700)
        self._setup()

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("PRAGMA journal_mode=WAL")
            yield db
        finally:
            db.close()

    def _setup(self):
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    provider TEXT NOT NULL CHECK(provider IN ('github','gitlab')),
                    repo TEXT NOT NULL,
                    event TEXT NOT NULL,
                    action TEXT NOT NULL,
                    ref TEXT NOT NULL,
                    sha TEXT NOT NULL,
                    base_ref TEXT NOT NULL DEFAULT '',
                    source_repo TEXT NOT NULL DEFAULT '',
                    actor TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL,
                    not_before INTEGER NOT NULL DEFAULT 0,
                    priority INTEGER NOT NULL DEFAULT 50,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    step_index INTEGER NOT NULL DEFAULT 0,
                    workflow_json TEXT NOT NULL DEFAULT '',
                    workflow_sha TEXT NOT NULL DEFAULT '',
                    image_id TEXT NOT NULL DEFAULT '',
                    deployment_app TEXT NOT NULL DEFAULT '',
                    workspace TEXT NOT NULL DEFAULT '',
                    log_path TEXT NOT NULL DEFAULT '',
                    artifact_path TEXT NOT NULL DEFAULT '',
                    artifact_sha256 TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    started_at INTEGER,
                    finished_at INTEGER,
                    duration_seconds REAL,
                    error TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    deployment_approved INTEGER NOT NULL DEFAULT 0,
                    deployment_started INTEGER NOT NULL DEFAULT 0,
                    delivery_id TEXT NOT NULL DEFAULT '',
                    evidence_json TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS jobs_state_created ON jobs(state, priority DESC, created_at);
                CREATE INDEX IF NOT EXISTS jobs_repo_sha ON jobs(provider, repo, sha);
                CREATE TABLE IF NOT EXISTS deliveries (
                    provider TEXT NOT NULL,
                    delivery_id TEXT NOT NULL,
                    body_sha256 TEXT NOT NULL,
                    received_at INTEGER NOT NULL,
                    outcome TEXT NOT NULL,
                    job_id TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(provider, delivery_id)
                );
                CREATE TABLE IF NOT EXISTS refs (
                    provider TEXT NOT NULL,
                    repo TEXT NOT NULL,
                    ref TEXT NOT NULL,
                    sha TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(provider, repo, ref)
                );
                CREATE TABLE IF NOT EXISTS poll_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    repo TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    detail TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS status_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    state TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    detail TEXT NOT NULL
                );
            """)
            # Upgrade databases created by an earlier bridge preview without
            # deleting queued jobs or execution evidence.
            columns={row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            migrations={
                "actor":"TEXT NOT NULL DEFAULT ''",
                "image_id":"TEXT NOT NULL DEFAULT ''",
                "deployment_app":"TEXT NOT NULL DEFAULT ''",
                "not_before":"INTEGER NOT NULL DEFAULT 0",
            }
            for name,declaration in migrations.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
        if self.path.exists() and os.name != "nt":
            os.chmod(self.path, 0o600)

    def recover_interrupted(self) -> int:
        # A Docker CLI can die while its daemon-managed container keeps running.
        # Never free a queue slot until that exact bridge container is confirmed stopped.
        with self._db() as db:
            running = [dict(row) for row in db.execute("SELECT id FROM jobs WHERE state='running'")]
        recovered = 0
        for row in running:
            if not shutil.which("docker"):
                continue
            name = f"open-ci-hub-ci-{row['id'][:16]}"
            try:
                probe = subprocess.run(["docker","inspect","--format","{{.State.Running}}",name],
                    stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=5,check=False)
                if probe.returncode == 0 and probe.stdout.strip().lower() == "true":
                    stopped = subprocess.run(["docker","stop","--time","3",name],
                        stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=10,check=False)
                    if stopped.returncode != 0:
                        continue
                    verify = subprocess.run(["docker","inspect","--format","{{.State.Running}}",name],
                        stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=5,check=False)
                    if verify.returncode == 0 and verify.stdout.strip().lower() == "true":
                        continue
                elif probe.returncode != 0:
                    if "no such object" not in (probe.stderr or "").lower() and "not found" not in (probe.stderr or "").lower():
                        continue
                with self._db() as db:
                    cursor = db.execute(
                        "UPDATE jobs SET state='interrupted', updated_at=?, finished_at=?, "
                        "error='Hub service restarted; the previous container was stopped or absent. Inspect logs before a manual retry.' "
                        "WHERE id=? AND state='running'", (now(), now(), row["id"]))
                    recovered += cursor.rowcount
            except (OSError,subprocess.TimeoutExpired):
                continue
        return recovered

    def enqueue(self, *, provider: str, repo: str, event: str, action: str, ref: str, sha: str,
                base_ref: str = "", source_repo: str = "", actor: str = "", delivery_id: str = "",
                manual_nonce: str = "") -> tuple[dict, bool]:
        config = read_config()
        repo_cfg = _repo_config(provider, repo, config)
        if not isinstance(ref, str) or not ref or len(ref) > 240 or "\x00" in ref:
            raise ValueError("Invalid branch/ref.")
        if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            raise ValueError("The exact commit SHA is missing or invalid.")
        if event in ("pull_request", "merge_request"):
            if not repo_cfg["allow_pull_requests"]:
                raise PermissionError("Pull/merge-request execution is disabled for this repository.")
            if not _branch_allowed(repo_cfg, base_ref):
                raise PermissionError("Pull/merge-request target branch is not allowlisted.")
            if source_repo.lower() != repo.lower():
                raise PermissionError("Fork pull/merge-request code is not allowed on this local PC.")
        elif not _branch_allowed(repo_cfg, ref):
            raise PermissionError("Branch is not on the local CI execution allowlist.")
        if event not in ("push", "poll", "pull_request", "merge_request", "manual"):
            raise ValueError("Unsupported local CI trigger type.")
        event_key = "pull-request" if event in ("pull_request", "merge_request") else "branch"
        seed = "|".join((provider, repo.lower(), event_key, ref, sha, base_ref if event_key == "pull-request" else "", manual_nonce))
        idempotency = hashlib.sha256(seed.encode()).hexdigest()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM jobs WHERE idempotency_key=?", (idempotency,)).fetchone()
            if existing:
                db.execute("COMMIT")
                return dict(existing), False
            pending = db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('pending','running','awaiting_approval')").fetchone()[0]
            total = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            if pending >= MAX_PENDING or total >= MAX_JOBS:
                db.execute("ROLLBACK")
                raise RuntimeError("CI queue capacity reached; no job was added.")
            job_id = secrets.token_hex(16)
            timestamp = now()
            db.execute("""INSERT INTO jobs
                (id,idempotency_key,provider,repo,event,action,ref,sha,base_ref,source_repo,actor,state,
                 priority,attempts,max_attempts,created_at,updated_at,delivery_id)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,'pending',50,0,?,?,?,?)""",
                 (job_id,idempotency,provider,repo,event,action,ref,sha,base_ref,source_repo,actor[:100],
                  MAX_ATTEMPTS,timestamp,timestamp,delivery_id))
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            db.execute("COMMIT")
            return dict(row), True

    def record_delivery(self, provider: str, delivery_id: str, body_sha: str, outcome: str, job_id: str = "") -> bool:
        if not DELIVERY_RE.fullmatch(delivery_id):
            raise ValueError("Provider delivery ID is missing or invalid.")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT body_sha256 FROM deliveries WHERE provider=? AND delivery_id=?",
                             (provider, delivery_id)).fetchone()
            if old:
                db.execute("COMMIT")
                return False
            db.execute("INSERT INTO deliveries(provider,delivery_id,body_sha256,received_at,outcome,job_id) VALUES(?,?,?,?,?,?)",
                       (provider,delivery_id,body_sha,now(),outcome,job_id))
            db.execute("COMMIT")
        return True

    def delivery_seen(self, provider: str, delivery_id: str) -> bool:
        with self._db() as db:
            return db.execute("SELECT 1 FROM deliveries WHERE provider=? AND delivery_id=?",
                              (provider,delivery_id)).fetchone() is not None

    def update_delivery(self, provider: str, delivery_id: str, outcome: str, job_id: str = "") -> None:
        with self._db() as db:
            db.execute("UPDATE deliveries SET outcome=?,job_id=? WHERE provider=? AND delivery_id=?",
                       (outcome,job_id,provider,delivery_id))

    def claim(self, max_concurrent: int) -> dict | None:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            running = db.execute("SELECT COUNT(*) FROM jobs WHERE state='running'").fetchone()[0]
            if running >= max_concurrent:
                db.execute("COMMIT")
                return None
            row = db.execute("SELECT * FROM jobs WHERE state='pending' AND cancel_requested=0 AND not_before<=? "
                             "ORDER BY priority DESC,created_at ASC LIMIT 1",(now(),)).fetchone()
            if not row:
                db.execute("COMMIT")
                return None
            timestamp = now()
            db.execute("UPDATE jobs SET state='running',attempts=attempts+1,started_at=?,updated_at=?,"
                       "finished_at=NULL,error='',summary='Execution started' WHERE id=? AND state='pending'",
                       (timestamp,timestamp,row["id"]))
            claimed = db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
            db.execute("COMMIT")
            return dict(claimed)

    def defer(self, job_id: str, reason: str, *, evidence: dict | None = None) -> dict:
        """Return a transiently blocked job to the durable queue without consuming an attempt."""
        message=reason[:1000]
        with self._db() as db:
            db.execute("UPDATE jobs SET state='pending',not_before=?,attempts=MAX(0,attempts-1),"
                       "updated_at=?,started_at=NULL,finished_at=NULL,error=?,"
                       "summary='Deferred by local resource guard; will retry automatically',evidence_json=? "
                       "WHERE id=? AND state='running'",
                       (now()+RESOURCE_RETRY_SECONDS,now(),message,
                        json.dumps(evidence or {},sort_keys=True),job_id))
            row=db.execute("SELECT * FROM jobs WHERE id=?",(job_id,)).fetchone()
            if not row:
                raise ValueError("Cannot defer a missing job.")
            return dict(row)

    def update_workflow(self, job_id: str, workflow: dict, workflow_sha: str, workspace: Path, log_path: Path) -> None:
        deployment=workflow.get("deployment") or {}
        with self._db() as db:
            db.execute("UPDATE jobs SET workflow_json=?,workflow_sha=?,deployment_app=?,workspace=?,log_path=?,updated_at=? WHERE id=?",
                       (json.dumps(workflow,sort_keys=True),workflow_sha,deployment.get("application",""),str(workspace),str(log_path),now(),job_id))

    def update_image_id(self, job_id: str, image_id: str) -> None:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise ValueError("Invalid pinned container image ID.")
        with self._db() as db:
            db.execute("UPDATE jobs SET image_id=?,updated_at=? WHERE id=?", (image_id,now(),job_id))

    def step_index(self, job_id: str) -> int:
        with self._db() as db:
            row = db.execute("SELECT step_index FROM jobs WHERE id=?", (job_id,)).fetchone()
            return int(row["step_index"]) if row else 0

    def set_step_index(self, job_id: str, index: int) -> None:
        with self._db() as db:
            db.execute("UPDATE jobs SET step_index=?,updated_at=? WHERE id=?", (index,now(),job_id))

    def mark_deployment_started(self, job_id: str) -> None:
        with self._db() as db:
            db.execute("UPDATE jobs SET deployment_started=1,updated_at=? WHERE id=?", (now(),job_id))

    def is_cancelled(self, job_id: str) -> bool:
        with self._db() as db:
            row = db.execute("SELECT cancel_requested,state FROM jobs WHERE id=?", (job_id,)).fetchone()
            return not row or row["state"] == "cancelled" or bool(row["cancel_requested"])

    def finish(self, job_id: str, state: str, *, error: str = "", summary: str = "",
               duration: float | None = None, evidence: dict | None = None,
               artifact_path: str = "", artifact_sha256: str = "") -> dict:
        if state not in ("passed","failed","cancelled","interrupted","blocked","awaiting_approval"):
            raise ValueError("Invalid terminal job state.")
        with self._db() as db:
            db.execute("UPDATE jobs SET state=?,updated_at=?,finished_at=?,duration_seconds=?,error=?,summary=?,"
                       "evidence_json=?,artifact_path=?,artifact_sha256=? WHERE id=?",
                       (state,now(),now() if state != "awaiting_approval" else None,duration,error[:1000],
                        summary[:500],json.dumps(evidence or {},sort_keys=True),artifact_path,artifact_sha256,job_id))
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else {}

    def approve(self, job_id: str, *, confirm: bool = False) -> dict:
        require_owner()
        if confirm is not True or not JOB_RE.fullmatch(job_id):
            raise PermissionError("Use approve JOB_ID --confirm as the local owner.")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row["state"] != "awaiting_approval":
                db.execute("ROLLBACK")
                raise ValueError("Only a job waiting for deployment approval can be approved.")
            try:
                workflow=json.loads(row["workflow_json"])
            except (ValueError,TypeError):
                db.execute("ROLLBACK")
                raise RuntimeError("Stored workflow is invalid; deployment approval refused.")
            deployment=workflow.get("deployment") if isinstance(workflow,dict) else None
            if not isinstance(deployment,dict):
                db.execute("ROLLBACK")
                raise PermissionError("This job has no signed deployment request.")
            workspace=Path(row["workspace"])
            try:
                _workspace_file(workspace,deployment["bundle"],MAX_RELEASE_BUNDLE_BYTES)
                _workspace_file(workspace,deployment["signature"],MAX_RELEASE_SIGNATURE_BYTES)
            except (OSError,ValueError,PermissionError) as exc:
                db.execute("ROLLBACK")
                raise PermissionError(f"Attach a valid offline release signature before approval: {str(exc)[:160]}") from None
            db.execute("UPDATE jobs SET deployment_approved=1,state='pending',not_before=0,updated_at=?,summary='Signed deployment approved by local owner' WHERE id=?",
                       (now(),job_id))
            result = dict(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
            db.execute("COMMIT")
            return result

    def retry(self, job_id: str, *, confirm: bool = False) -> dict:
        require_owner()
        if confirm is not True or not JOB_RE.fullmatch(job_id):
            raise PermissionError("Use retry JOB_ID --confirm as the local owner.")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row["state"] not in ("failed","interrupted"):
                db.execute("ROLLBACK")
                raise ValueError("Only failed/interrupted jobs can be retried.")
            if row["attempts"] >= row["max_attempts"] or row["deployment_started"]:
                db.execute("ROLLBACK")
                raise PermissionError("Retry limit reached or a deployment step has started; create a new reviewed job instead.")
            db.execute("UPDATE jobs SET state='pending',not_before=0,step_index=0,workflow_json='',workflow_sha='',image_id='',deployment_app='',workspace='',"
                       "log_path='',artifact_path='',artifact_sha256='',error='',summary='Manually retried by local owner',"
                       "cancel_requested=0,started_at=NULL,finished_at=NULL,updated_at=? WHERE id=?",
                       (now(),job_id))
            result = dict(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
            db.execute("COMMIT")
            return result

    def cancel(self, job_id: str, *, confirm: bool = False) -> dict:
        require_owner()
        if confirm is not True or not JOB_RE.fullmatch(job_id):
            raise PermissionError("Use cancel JOB_ID --confirm as the local owner.")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row["state"] in ("passed","failed","cancelled","blocked"):
                db.execute("ROLLBACK")
                raise ValueError("Job is missing or already terminal.")
            if row["state"] in ("pending","awaiting_approval"):
                db.execute("UPDATE jobs SET state='cancelled',updated_at=?,finished_at=?,summary='Cancelled by local owner' WHERE id=?",
                           (now(),now(),job_id))
            else:
                db.execute("UPDATE jobs SET cancel_requested=1,updated_at=?,summary='Cancellation requested by local owner' WHERE id=?",
                           (now(),job_id))
            result = dict(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
            db.execute("COMMIT")
        with ACTIVE_LOCK:
            process = ACTIVE_PROCESSES.get(job_id)
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
        return result

    def get(self, job_id: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def jobs(self, limit: int = 50) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT id,provider,repo,event,action,ref,sha,base_ref,actor,state,attempts,max_attempts,"
                              "created_at,updated_at,started_at,finished_at,duration_seconds,error,summary,not_before,"
                              "workflow_sha,image_id,deployment_app,artifact_sha256,delivery_id FROM jobs ORDER BY created_at DESC LIMIT ?",
                              (max(1,min(limit,200)),)).fetchall()
            return [dict(row) for row in rows]

    def status(self) -> dict:
        config = read_config()
        with self._db() as db:
            counts = {row["state"]: row["n"] for row in db.execute("SELECT state,COUNT(*) AS n FROM jobs GROUP BY state")}
            last_delivery = db.execute("SELECT provider,received_at,outcome,job_id FROM deliveries ORDER BY received_at DESC LIMIT 1").fetchone()
            last_poll = db.execute("SELECT ts,provider,repo,outcome,detail FROM poll_log ORDER BY id DESC LIMIT 10").fetchall()
            last_reports = db.execute("SELECT job_id,ts,provider,state,outcome,detail FROM status_reports ORDER BY id DESC LIMIT 10").fetchall()
            last_success = db.execute("SELECT provider,repo,MAX(finished_at) AS finished_at FROM jobs WHERE state='passed' GROUP BY provider,repo").fetchall()
        try:
            import machine_policy
            from resource_guard import memory_available_mb
            from native_runner_observer import runner_reservations
            host_resources = {"memory_available_mb":memory_available_mb(),
                              "telemetry":machine_policy.sample(),
                              "native_runner_reservations":runner_reservations()}
        except Exception as exc:
            host_resources = {"available":False,"reason":type(exc).__name__}
        return {
            "service": "open-ci-hub-ci-execution-bridge",
            "database": str(self.path),
            "repos": [{"provider":x["provider"],"repo":x["repo"],"branches":x["branches"],
                       "allow_pull_requests":x["allow_pull_requests"],"allow_network":x["allow_network"]}
                      for x in config["repos"]],
            "queue": counts,
            "max_concurrent": config["max_concurrent"],
            "last_delivery": dict(last_delivery) if last_delivery else None,
            "last_success_by_repo": [dict(x) for x in last_success],
            "host_resources": host_resources,
            "recent_polls": [dict(x) for x in last_poll],
            "recent_status_reports": [dict(x) for x in last_reports],
            "note": "A provider status is runner-reported evidence, not independent attestation.",
        }

    def note_poll(self, provider: str, repo: str, outcome: str, detail: str = "") -> None:
        with self._db() as db:
            db.execute("INSERT INTO poll_log(ts,provider,repo,outcome,detail) VALUES(?,?,?,?,?)",
                       (now(),provider,repo,outcome,detail[:300]))

    def note_status(self, job_id: str, provider: str, state: str, outcome: str, detail: str = "") -> None:
        with self._db() as db:
            db.execute("INSERT INTO status_reports(job_id,ts,provider,state,outcome,detail) VALUES(?,?,?,?,?,?)",
                       (job_id,now(),provider,state,outcome,detail[:300]))

    def get_ref(self, provider: str, repo: str, ref: str) -> str:
        with self._db() as db:
            row = db.execute("SELECT sha FROM refs WHERE provider=? AND repo=? AND ref=?",
                             (provider,repo,ref)).fetchone()
            return row["sha"] if row else ""

    def set_ref(self, provider: str, repo: str, ref: str, sha: str) -> None:
        with self._db() as db:
            db.execute("INSERT INTO refs(provider,repo,ref,sha,updated_at) VALUES(?,?,?,?,?) "
                       "ON CONFLICT(provider,repo,ref) DO UPDATE SET sha=excluded.sha,updated_at=excluded.updated_at",
                       (provider,repo,ref,sha,now()))


def _protected_branch(provider: str, repo: str, branch: str) -> bool:
    encoded_repo = quote(repo, safe="")
    encoded_branch = quote(branch, safe="")
    if provider == "github":
        data = invoke_json(["gh","api",f"repos/{repo}/branches/{encoded_branch}"])
        return isinstance(data,dict) and data.get("protected") is True
    data = invoke_json(["glab","api",f"projects/{encoded_repo}/repository/branches/{encoded_branch}"])
    return isinstance(data,dict) and data.get("protected") is True


def _normalise_event(provider: str, event_name: str, payload: dict) -> tuple[dict | None, str]:
    if provider == "github":
        repo_obj = payload.get("repository")
        if not isinstance(repo_obj,dict):
            return None,"repository missing"
        repo = repo_obj.get("full_name")
        try:
            repo = safe_repo(repo)
        except (ValueError,TypeError):
            return None,"invalid repository"
        sender = payload.get("sender",{})
        actor = sender.get("login","") if isinstance(sender,dict) else ""
        if event_name == "push":
            ref = payload.get("ref","")
            prefix = "refs/heads/"
            if not isinstance(ref,str) or not ref.startswith(prefix):
                return None,"non-branch ref ignored"
            branch = ref[len(prefix):]
            sha = payload.get("after","")
            if not SHA_RE.fullmatch(str(sha)) or set(sha) == {"0"}:
                return None,"deleted or invalid ref ignored"
            return {"provider":provider,"repo":repo,"event":"push","action":"push","ref":branch,
                    "sha":sha,"base_ref":"","source_repo":repo,"actor":str(actor)[:100]}, ""
        if event_name == "pull_request":
            pr = payload.get("pull_request")
            action = payload.get("action","")
            if action not in ("opened","reopened","synchronize","ready_for_review","edited") or not isinstance(pr,dict):
                return None,"pull-request action ignored"
            head,base = pr.get("head"),pr.get("base")
            if not isinstance(head,dict) or not isinstance(base,dict):
                return None,"pull-request refs missing"
            source = head.get("repo")
            source_repo = source.get("full_name") if isinstance(source,dict) else ""
            if not isinstance(source_repo,str) or source_repo.lower() != repo.lower():
                return None,"fork pull-request rejected"
            branch,sha,base_ref = head.get("ref"),head.get("sha"),base.get("ref")
            if not isinstance(branch,str) or not isinstance(base_ref,str) or not SHA_RE.fullmatch(str(sha)):
                return None,"invalid pull-request SHA/ref"
            pr_user=pr.get("user",{})
            pr_actor=pr_user.get("login","") if isinstance(pr_user,dict) else actor
            return {"provider":provider,"repo":repo,"event":"pull_request","action":action,
                    "ref":branch,"sha":sha,"base_ref":base_ref,"source_repo":source_repo,
                    "actor":str(pr_actor or actor)[:100]}, ""
        return None,"event type ignored"
    if provider == "gitlab":
        project = payload.get("project")
        if not isinstance(project,dict):
            return None,"project missing"
        try:
            repo = safe_repo(project.get("path_with_namespace"))
        except (ValueError,TypeError):
            return None,"invalid project path"
        user = payload.get("user",{})
        actor = payload.get("user_username") or (user.get("username") if isinstance(user,dict) else "") or ""
        if payload.get("object_kind") == "push" or event_name.lower() == "push hook":
            ref = payload.get("ref","")
            prefix = "refs/heads/"
            if not isinstance(ref,str) or not ref.startswith(prefix):
                return None,"non-branch ref ignored"
            branch = ref[len(prefix):]
            sha = payload.get("checkout_sha") or payload.get("after")
            if not isinstance(sha,str) or not SHA_RE.fullmatch(sha) or set(sha)=={"0"}:
                return None,"deleted or invalid ref ignored"
            return {"provider":provider,"repo":repo,"event":"push","action":"push","ref":branch,
                    "sha":sha,"base_ref":"","source_repo":repo,"actor":str(actor)[:100]}, ""
        if payload.get("object_kind") == "merge_request" or event_name.lower() == "merge request hook":
            attrs = payload.get("object_attributes")
            if not isinstance(attrs,dict) or attrs.get("action") not in ("open","reopen","update","approved"):
                return None,"merge-request action ignored"
            source_project = attrs.get("source_project_id")
            target_project = attrs.get("target_project_id")
            if type(source_project) is not int or type(target_project) is not int or source_project != target_project:
                return None,"fork or unverified merge-request project identity rejected"
            source = attrs.get("source",{}) if isinstance(attrs.get("source"),dict) else {}
            target = attrs.get("target",{}) if isinstance(attrs.get("target"),dict) else {}
            source_repo = source.get("path_with_namespace") or repo
            branch = attrs.get("source_branch")
            base_ref = attrs.get("target_branch")
            commit = attrs.get("last_commit",{}) if isinstance(attrs.get("last_commit"),dict) else {}
            sha = commit.get("id") or attrs.get("last_commit_id")
            if not isinstance(branch,str) or not isinstance(base_ref,str) or not isinstance(sha,str) or not SHA_RE.fullmatch(sha):
                return None,"invalid merge-request SHA/ref"
            return {"provider":provider,"repo":repo,"event":"merge_request","action":attrs.get("action","update"),
                    "ref":branch,"sha":sha,"base_ref":base_ref,"source_repo":source_repo,
                    "actor":str(actor)[:100]}, ""
        return None,"event type ignored"
    return None,"unsupported provider"


def _provider_status_impl(job: dict, state: str, description: str) -> None:
    """Publish provider status; caller records success/failure without hiding local state."""
    provider, repo, sha = job["provider"], job["repo"], job["sha"]
    description = re.sub(r"[\r\n\t]+"," ",description)[:140]
    config = read_config()
    base = config.get("public_job_base_url","").rstrip("/")
    target = f"{base}/?job={job['id']}" if base else ""
    if provider == "github":
        mapping = {"pending":"pending","running":"pending","passed":"success","failed":"failure",
                   "cancelled":"error","interrupted":"error","blocked":"error","awaiting_approval":"pending"}
        args = ["gh","api","--method","POST",f"repos/{repo}/statuses/{sha}",
                "-f",f"state={mapping.get(state,'error')}",
                "-f","context=Open CI Hub / local execution",
                "-f",f"description={description}"]
        if target:
            args += ["-f",f"target_url={target}"]
        invoke(args,timeout=30)
    elif provider == "gitlab":
        mapping = {"pending":"pending","running":"running","passed":"success","failed":"failed",
                   "cancelled":"canceled","interrupted":"failed","blocked":"failed","awaiting_approval":"pending"}
        args = ["glab","api","--method","POST",
                f"projects/{quote(repo,safe='')}/statuses/{sha}",
                "--field",f"state={mapping.get(state,'failed')}",
                "--field","name=Open CI Hub / local execution",
                "--field",f"description={description}",
                "--field",f"ref={job['ref']}"]
        if target:
            args += ["--field",f"target_url={target}"]
        invoke(args,timeout=30)


def _provider_status(job: dict, state: str, description: str) -> None:
    try:
        _provider_status_impl(job,state,description)
    except Exception as exc:
        try:
            Queue(DB_PATH).note_status(job["id"],job["provider"],state,"error",
                                       f"{type(exc).__name__}: {str(exc)[:200]}")
        except Exception:
            pass
        raise
    else:
        try:
            Queue(DB_PATH).note_status(job["id"],job["provider"],state,"success","Provider accepted the commit status.")
        except Exception:
            pass


def _safe_redact(value: str) -> str:
    try:
        from execution_evidence import redact
        return redact(value)[:MAX_OUTPUT_BYTES]
    except Exception:
        value = re.sub(r"(?i)(token|secret|password|api[_-]?key)\s*[=:]\s*\S+",r"\1=[REDACTED]",value)
        value = re.sub(r"(?i)Bearer\s+\S+","Bearer [REDACTED]",value)
        return value[-MAX_OUTPUT_BYTES:]


def _run_bounded(argv: list[str], *, cwd: Path | None = None, timeout: int = 30,
                 env: dict | None = None, job_id: str = "", should_cancel=None) -> dict:
    started = time.monotonic()
    try:
        proc = subprocess.Popen(argv,cwd=str(cwd) if cwd else None,env=env,stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,shell=False,
                                close_fds=True,start_new_session=True)
    except OSError as exc:
        raise RuntimeError(f"Could not start {Path(argv[0]).name}: {type(exc).__name__}.") from None
    if job_id:
        with ACTIVE_LOCK:
            ACTIVE_PROCESSES[job_id] = proc
    output = bytearray()
    total = [0]
    output_lock = threading.Lock()
    assert proc.stdout is not None
    def drain():
        while True:
            try:
                chunk = proc.stdout.read(4096)
            except OSError:
                break
            if not chunk:
                break
            total[0] += len(chunk)
            with output_lock:
                room = MAX_OUTPUT_BYTES - len(output)
                if room > 0:
                    output.extend(chunk[:room])
    reader = threading.Thread(target=drain,daemon=True,name="ci-bridge-output")
    reader.start()
    timed_out = False
    cancelled = False
    resource_abort = False
    deadline = started + timeout
    try:
        while proc.poll() is None:
            reason = should_cancel() if job_id and should_cancel else ""
            cancelled = reason is True or reason == "cancelled"
            resource_abort = reason == "resource"
            if STOP.is_set() or cancelled or resource_abort or time.monotonic() >= deadline:
                timed_out = not cancelled and not resource_abort
                try:
                    os.killpg(proc.pid,signal.SIGTERM)
                except (OSError,ProcessLookupError):
                    pass
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid,signal.SIGKILL)
                    except (OSError,ProcessLookupError):
                        pass
                break
            time.sleep(0.05)
        try:
            code = proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            code = 124
        reader.join(timeout=3)
        if reader.is_alive():
            try:
                os.killpg(proc.pid,signal.SIGKILL)
            except (OSError,ProcessLookupError):
                pass
            reader.join(timeout=1)
    finally:
        # An exception in the cancellation/resource callback must never orphan
        # a process that keeps the queue slot and container alive.
        if proc.poll() is None:
            try:
                os.killpg(proc.pid,signal.SIGTERM)
            except (OSError,ProcessLookupError):
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid,signal.SIGKILL)
                except (OSError,ProcessLookupError):
                    pass
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        if job_id:
            with ACTIVE_LOCK:
                ACTIVE_PROCESSES.pop(job_id,None)
        try:
            proc.stdout.close()
        except OSError:
            pass
    with output_lock:
        decoded = bytes(output).decode("utf-8","replace")
    return {"exit_code":int(code),"timed_out":timed_out,"cancelled":cancelled,"resource_abort":resource_abort,
            "duration_seconds":round(time.monotonic()-started,3),
            "output":_safe_redact(decoded),"output_truncated":total[0]>len(output)}


def _resource_admission(config: dict, workflow: dict) -> dict:
    from resource_guard import memory_available_mb
    import machine_policy
    from native_runner_observer import runner_reservations
    telemetry = machine_policy.sample()
    decision = machine_policy.admission("balanced",telemetry,1)
    if decision.get("max_parallel",0) < 1:
        raise ResourceDeferred(f"Resource admission deferred: {decision.get('reason','host pressure')}.")
    if telemetry.get("temperature_c") is None:
        raise ResourceDeferred("Resource admission deferred: CPU package temperature is unavailable.")
    try:
        disk = shutil.disk_usage(WORK_ROOT.parent if WORK_ROOT.parent.exists() else ROOT)
    except OSError:
        raise ResourceDeferred("Resource admission deferred: disk capacity cannot be measured.") from None
    if disk.free < config["min_free_disk_gb"] * 1024**3:
        raise ResourceDeferred("Resource admission deferred: below the configured free-disk reserve.")
    available = memory_available_mb()
    runners = runner_reservations()
    if available is None or runners is None:
        raise ResourceDeferred("Resource admission deferred: RAM or native-runner reservations cannot be measured.")
    # MemAvailable already reflects memory currently used by idle runners. Subtracting
    # every runner's full cgroup limit double-counts that memory; a live watchdog below
    # terminates the isolated job if real RAM, disk or thermal headroom is breached.
    required = config["min_free_ram_mb"] + workflow["memory_mb"]
    if available < required:
        raise ResourceDeferred(f"Resource admission deferred: {available} MiB available, {required} MiB required for the configured desktop reserve and job.")
    return {"admitted":True,"memory_available_mb":available,"runner_reservations":runners,
            "thermal":decision,"workflow_memory_mb":workflow["memory_mb"],"required_free_ram_mb":required,
            "free_disk_bytes":disk.free}


def _git_command(argv: list[str], *, cwd: Path | None = None, timeout: int = 90) -> str:
    result = _run_bounded(argv,cwd=cwd,timeout=timeout)
    if result["timed_out"] or result["exit_code"] != 0:
        raise RuntimeError(f"Git checkout step failed ({Path(argv[0]).name}, exit {result['exit_code']}).")
    return result["output"]


def _checkout(job: dict, workspace: Path, *, workflow_sha: str = "") -> tuple[str,str]:
    provider, repo, sha = job["provider"], job["repo"], job["sha"]
    if workspace.exists():
        if workspace.is_symlink() or not workspace.is_dir():
            raise PermissionError("Unsafe existing job workspace.")
        # A deployment-approved job retains the workspace with no .git directory.
        if not (workspace / ".git").exists() and job.get("workflow_json"):
            return job["workflow_sha"], ""
        shutil.rmtree(workspace)
    workspace.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if provider == "github":
        _git_command(["gh","repo","clone",repo,str(workspace)],timeout=120)
    else:
        _git_command(["glab","repo","clone",repo,str(workspace)],timeout=120)
    if (workspace / ".git").is_symlink() or not (workspace / ".git").is_dir():
        raise RuntimeError("Provider clone did not create a safe Git worktree.")
    _git_command(["git","-C",str(workspace),"fetch","--no-tags","--depth=1","origin",sha],timeout=120)
    _git_command(["git","-C",str(workspace),"checkout","--detach","FETCH_HEAD"],timeout=60)
    actual = _git_command(["git","-C",str(workspace),"rev-parse","HEAD"]).strip()
    if actual.lower() != sha.lower():
        raise RuntimeError("Checked-out commit SHA does not match the requested provider event.")
    if job["event"] in ("pull_request","merge_request"):
        base_ref = job["base_ref"]
        _git_command(["git","-C",str(workspace),"fetch","--no-tags","--depth=1","origin",f"refs/heads/{base_ref}"],timeout=120)
        base_actual = _git_command(["git","-C",str(workspace),"rev-parse","FETCH_HEAD"]).strip()
        if workflow_sha and SHA_RE.fullmatch(workflow_sha):
            # The webhook's base SHA is used only when available and fetched explicitly.
            _git_command(["git","-C",str(workspace),"fetch","--no-tags","--depth=1","origin",workflow_sha],timeout=120)
            base_actual = _git_command(["git","-C",str(workspace),"rev-parse","FETCH_HEAD"]).strip()
        trusted = _git_command(["git","-C",str(workspace),"show",f"{base_actual}:{WORKFLOW_FILE}"],timeout=15)
    else:
        base_actual = actual
        trusted = _git_command(["git","-C",str(workspace),"show",f"{actual}:{WORKFLOW_FILE}"],timeout=15)
    workflow = validate_workflow(trusted, _repo_config(provider,repo))
    # No Git metadata or credentials/config survive into the container.
    shutil.rmtree(workspace / ".git")
    return base_actual, json.dumps(workflow,sort_keys=True)


def validate_workflow(raw: str, repo_cfg: dict) -> dict:
    if not isinstance(raw,str) or len(raw.encode("utf-8")) > 32*1024:
        raise ValueError(f"{WORKFLOW_FILE} is missing or exceeds 32 KiB.")
    try:
        data = tomllib.loads(raw)
    except (tomllib.TOMLDecodeError,ValueError):
        raise ValueError(f"{WORKFLOW_FILE} is invalid TOML.") from None
    if data.get("version") != 1:
        raise ValueError("Workflow version must be 1.")
    image = data.get("image")
    if not isinstance(image,str) or not IMAGE_RE.fullmatch(image) or image not in repo_cfg["allowed_images"]:
        raise PermissionError("Workflow image is not explicitly approved in the local repository policy.")
    network = data.get("network","none")
    if network not in ("none","bridge"):
        raise ValueError("Workflow network must be none or bridge.")
    if network == "bridge" and not repo_cfg["allow_network"]:
        raise PermissionError("Workflow requested network access, which is disabled by local policy.")
    memory = data.get("memory_mb",2048)
    cpus = data.get("cpus",1.0)
    timeout = data.get("timeout_seconds",600)
    if type(memory) is not int or not 512 <= memory <= 4096:
        raise ValueError("Workflow memory_mb must be 512–4096.")
    if type(cpus) not in (int,float) or not 0.5 <= cpus <= 1.5:
        raise ValueError("Workflow cpus must be 0.5–1.5.")
    if type(timeout) is not int or not 1 <= timeout <= MAX_JOB_SECONDS:
        raise ValueError("Workflow timeout_seconds must be 1–1800.")
    steps = data.get("steps")
    if not isinstance(steps,list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError("Workflow must contain 1–20 [[steps]] entries.")
    checked=[]
    names=set()
    total=0
    for step in steps:
        if not isinstance(step,dict):
            raise ValueError("Every workflow step must be a table.")
        name,run = step.get("name"),step.get("run")
        stage = step.get("stage","test")
        seconds = step.get("timeout_seconds",min(300,timeout))
        if not isinstance(name,str) or not STEP_RE.fullmatch(name) or name in names:
            raise ValueError("Workflow step names must be unique, short and safe.")
        names.add(name)
        if not isinstance(run,str) or not run.strip() or len(run)>4000 or "\x00" in run:
            raise ValueError(f"Invalid run command in step {name}.")
        if stage not in ("test","build"):
            raise ValueError(f"Unsupported stage in step {name}; deployments use the signed release manager, never an arbitrary shell step.")
        if type(seconds) is not int or not 1 <= seconds <= MAX_STEP_SECONDS:
            raise ValueError(f"Step {name} timeout_seconds must be 1–900.")
        total += seconds
        checked.append({"name":name,"run":run,"stage":stage,"timeout_seconds":seconds})
    if total > timeout or total > MAX_JOB_SECONDS:
        raise ValueError("Sum of step timeouts exceeds the workflow timeout.")
    artifacts=data.get("artifacts",[])
    if not isinstance(artifacts,list) or len(artifacts)>20:
        raise ValueError("artifacts must be a list of at most 20 relative paths.")
    checked_artifacts=[]
    for item in artifacts:
        if not isinstance(item,str) or len(item)>200 or "\x00" in item:
            raise ValueError("Invalid artifact path.")
        path=PurePosixPath(item)
        if path.is_absolute() or not path.parts or any(part in ("..",".") for part in path.parts):
            raise ValueError("Artifact paths must be relative and may not traverse parents.")
        checked_artifacts.append(str(path))
    deployment=data.get("deployment")
    checked_deployment=None
    if deployment is not None:
        if not isinstance(deployment,dict) or set(deployment)!={"application","bundle","signature","signer"}:
            raise ValueError("[deployment] must contain application, bundle, signature and signer only.")
        app,bundle,signature,signer=(deployment.get("application"),deployment.get("bundle"),
                                     deployment.get("signature"),deployment.get("signer"))
        if not isinstance(app,str) or not DEPLOY_APP_RE.fullmatch(app):
            raise ValueError("Invalid signed-release application ID.")
        if not isinstance(signer,str) or not SIGNER_RE.fullmatch(signer):
            raise ValueError("Invalid approved release signer ID.")
        paths={}
        for key,value in (("bundle",bundle),("signature",signature)):
            if not isinstance(value,str) or len(value)>200 or "\x00" in value:
                raise ValueError(f"Invalid deployment {key} path.")
            path=PurePosixPath(value)
            if path.is_absolute() or not path.parts or any(part in ("..",".") for part in path.parts):
                raise ValueError("Deployment paths must be relative and may not traverse parents.")
            paths[key]=str(path)
        if paths["bundle"]==paths["signature"]:
            raise ValueError("Release bundle and signature must be separate files.")
        if not any(paths["bundle"]==artifact or paths["bundle"].startswith(artifact.rstrip("/")+"/") for artifact in checked_artifacts):
            raise ValueError("The signed release bundle must be included in retained artifacts.")
        checked_deployment={"application":app,"bundle":paths["bundle"],
                            "signature":paths["signature"],"signer":signer}
    return {"version":1,"image":image,"network":network,"memory_mb":memory,"cpus":float(cpus),
            "timeout_seconds":timeout,"steps":checked,"artifacts":checked_artifacts,
            "deployment":checked_deployment}


def _resource_abort_reason(config: dict) -> str:
    """Live guard checked while a container command runs."""
    try:
        from resource_guard import memory_available_mb
        import machine_policy
        available=memory_available_mb()
        telemetry=machine_policy.sample()
        if available is None or available < config["min_free_ram_mb"]:
            return "resource"
        temperature=telemetry.get("temperature_c")
        if temperature is None or temperature >= 90:
            return "resource"
        pressure=telemetry.get("memory_pressure_pct")
        if isinstance(pressure,(int,float)) and pressure >= 25:
            return "resource"
        disk=telemetry.get("disk")
        if not isinstance(disk,dict) or not isinstance(disk.get("min_free_mb"),(int,float)):
            return "resource"
        if disk["min_free_mb"] < config["min_free_disk_gb"]*1024:
            return "resource"
    except Exception:
        return "resource"
    return ""


def _cache_dir(job: dict, image_id: str) -> Path | None:
    # Unreviewed PR code never sees or modifies a cache shared with trusted jobs.
    if job["event"] in ("pull_request","merge_request"):
        return None
    _private_dir(CACHE_ROOT)
    key=hashlib.sha256(f"{job['provider']}|{job['repo'].lower()}|{image_id}".encode()).hexdigest()[:24]
    path=CACHE_ROOT/key
    if path.is_symlink():
        raise PermissionError("Symlinked dependency cache is forbidden.")
    if path.exists():
        size=0
        for root,dirs,files in os.walk(path,followlinks=False):
            dirs[:]=[d for d in dirs if not (Path(root)/d).is_symlink()]
            for filename in files:
                item=Path(root)/filename
                if item.is_symlink():continue
                try:size+=item.stat().st_size
                except OSError:continue
                if size>MAX_CACHE_BYTES:break
            if size>MAX_CACHE_BYTES:break
        if size>MAX_CACHE_BYTES:
            shutil.rmtree(path)
    _private_dir(path)
    return path


def _container_argv(job: dict, workflow: dict, image_id: str, step: dict, workspace: Path,
                    cache_path: Path | None = None) -> list[str]:
    memory=workflow["memory_mb"]
    argv=[
        "docker","run","--rm","--pull","never",
        "--name",f"open-ci-hub-ci-{job['id'][:16]}",
        "--network",workflow["network"],
        "--memory",f"{memory}m","--memory-swap",f"{memory}m",
        "--cpus",str(workflow["cpus"]),"--pids-limit","128",
        "--read-only","--cap-drop=ALL","--security-opt=no-new-privileges:true",
        "--user",f"{os.getuid()}:{os.getgid()}",
        "--tmpfs","/tmp:rw,nosuid,nodev,size=512m",
        "--tmpfs","/home/ci:rw,nosuid,nodev,size=128m",
        "--env","CI=1","--env","HOME=/home/ci","--env","TMPDIR=/tmp",
        "--volume",f"{workspace}:/workspace:rw",
    ]
    if cache_path is not None:
        argv.extend(["--volume",f"{cache_path}:/cache:rw",
                     "--env","PIP_CACHE_DIR=/cache/pip",
                     "--env","npm_config_cache=/cache/npm",
                     "--env","YARN_CACHE_FOLDER=/cache/yarn",
                     "--env","CARGO_HOME=/cache/cargo"])
    argv.extend(["--workdir","/workspace",image_id,"/bin/sh","-lc",step["run"]])
    return argv


def _image_id(image: str) -> str:
    if not shutil.which("docker"):
        raise RuntimeError("Docker Engine is unavailable; no host-side execution fallback is permitted.")
    result=_run_bounded(["docker","image","inspect","--format","{{.Id}}",image],timeout=15)
    if result["exit_code"] != 0 or result["timed_out"]:
        raise RuntimeError(f"Approved image is not present locally: {image}. Pull/review it explicitly as the owner first.")
    image_id=result["output"].strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}",image_id):
        raise RuntimeError("Docker returned an invalid image ID.")
    return image_id


def _collect_artifacts(workspace: Path, patterns: list[str], job_id: str) -> tuple[str,str]:
    if not patterns:
        return "",""
    _private_dir(ARTIFACT_ROOT)
    destination=ARTIFACT_ROOT/f"{job_id}.tar.gz"
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Artifact destination already exists.")
    files=[]
    total=0
    for pattern in patterns:
        target=workspace.joinpath(*PurePosixPath(pattern).parts)
        if target.is_symlink():
            raise PermissionError("Symlink artifacts are blocked.")
        if not target.exists():
            continue
        candidates=[target] if target.is_file() else [p for p in target.rglob("*") if p.is_file()]
        for path in candidates:
            if path.is_symlink() or not path.resolve().is_relative_to(workspace.resolve()):
                raise PermissionError("Artifact escaped the job workspace.")
            st=path.stat()
            if st.st_size<0:
                raise ValueError("Invalid artifact file size.")
            total+=st.st_size
            files.append(path)
            if len(files)>MAX_ARTIFACT_FILES or total>MAX_ARTIFACT_BYTES:
                raise ValueError("Artifact collection exceeded its file/size limit.")
    fd,tmp=tempfile.mkstemp(prefix=".artifact-",dir=str(ARTIFACT_ROOT))
    os.close(fd)
    try:
        with tarfile.open(tmp,"w:gz") as archive:
            for path in files:
                if path.is_symlink() or not path.is_file():
                    raise PermissionError("Only regular artifact files are accepted.")
                archive.add(path,arcname=path.relative_to(workspace).as_posix(),recursive=False)
        os.chmod(tmp,0o600)
        os.replace(tmp,destination)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    digest=hashlib.sha256(destination.read_bytes()).hexdigest()
    return str(destination),digest


def _workspace_file(workspace: Path, relative: str, max_bytes: int | None = None) -> Path:
    if workspace.is_symlink() or not workspace.is_dir():
        raise PermissionError("Job workspace is missing or symlinked.")
    rel=PurePosixPath(relative)
    if rel.is_absolute() or not rel.parts or any(part in ("..",".") for part in rel.parts):
        raise PermissionError("Workspace path traversal is blocked.")
    target=workspace
    for part in rel.parts:
        target=target/part
        if target.is_symlink():
            raise PermissionError("Symlinked workspace paths are blocked.")
    if not target.is_file() or not target.resolve().is_relative_to(workspace.resolve()):
        raise FileNotFoundError("Expected regular file is missing from the job workspace.")
    if max_bytes is not None and target.stat().st_size>max_bytes:
        raise ValueError("Workspace file exceeds its size limit.")
    return target


def attach_signature(job_id: str, source_path: str, *, confirm: bool = False) -> dict:
    require_owner()
    if confirm is not True or not JOB_RE.fullmatch(job_id):
        raise PermissionError("Use attach-signature JOB_ID SOURCE_PATH --confirm as the local owner.")
    queue=Queue(DB_PATH)
    job=queue.get(job_id)
    if not job or job["state"]!="awaiting_approval":
        raise ValueError("A signature can only be attached to a job awaiting deployment approval.")
    try:
        workflow=json.loads(job["workflow_json"])
    except (ValueError,TypeError):
        raise RuntimeError("Stored workflow is invalid.") from None
    deployment=workflow.get("deployment") if isinstance(workflow,dict) else None
    if not isinstance(deployment,dict):
        raise PermissionError("This job has no signed deployment request.")
    source=Path(os.path.expanduser(source_path))
    if source.is_symlink() or not source.is_file():
        raise PermissionError("Signature source must be a regular non-symlink file.")
    st=source.stat()
    if st.st_uid!=os.geteuid() or st.st_size<1 or st.st_size>MAX_RELEASE_SIGNATURE_BYTES:
        raise PermissionError("Signature source must be owner-owned and no larger than 10 KiB.")
    workspace=Path(job["workspace"])
    relative=PurePosixPath(deployment["signature"])
    target=workspace.joinpath(*relative.parts)
    if not target.parent.is_dir() or target.parent.is_symlink() or target.exists() or target.is_symlink():
        raise PermissionError("Signature destination parent must exist and the destination must be new.")
    if not target.parent.resolve().is_relative_to(workspace.resolve()):
        raise PermissionError("Signature destination escapes the job workspace.")
    payload=source.read_bytes()
    fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,"O_NOFOLLOW",0),0o600)
    with os.fdopen(fd,"wb") as stream:
        stream.write(payload);stream.flush();os.fsync(stream.fileno())
    return {"attached":True,"job_id":job_id,"destination":str(target),
            "sha256":hashlib.sha256(payload).hexdigest(),"bytes":len(payload),
            "note":"The signed release manager will verify the signature against the locally approved offline signer before deployment."}


def _verify_release_bundle(bundle: Path, app: str, expected_sha: str) -> dict:
    if bundle.is_symlink() or not bundle.is_file() or bundle.stat().st_size>MAX_RELEASE_BUNDLE_BYTES:
        raise PermissionError("Release bundle is missing, symlinked or too large.")
    total=0
    with tarfile.open(bundle,"r:gz") as archive:
        members=archive.getmembers()
        if not members or len(members)>5000:
            raise ValueError("Release archive has an invalid number of entries.")
        release_members=[]
        seen=set()
        for member in members:
            path=PurePosixPath(member.name)
            if path.is_absolute() or not path.parts or any(part in ("..",".") for part in path.parts):
                raise PermissionError("Release archive contains a path traversal.")
            if member.name in seen:
                raise ValueError("Release archive contains duplicate paths.")
            seen.add(member.name)
            if not member.isfile():
                raise PermissionError("Release archive may contain regular files only.")
            total+=member.size
            if member.size<0 or total>150*1024*1024:
                raise ValueError("Release archive exceeds the unpacked size limit.")
            if path.as_posix()=="release.json":
                release_members.append(member)
        if len(release_members)!=1 or release_members[0].size>100*1024:
            raise ValueError("Release archive must contain exactly one small root release.json.")
        stream=archive.extractfile(release_members[0])
        if stream is None:
            raise ValueError("Release manifest cannot be read.")
        try:manifest=json.loads(stream.read(100*1024+1))
        except (ValueError,UnicodeError):
            raise ValueError("Release manifest is invalid JSON.") from None
    from release_manager import Releases
    Releases._manifest(manifest,app)
    if manifest.get("source_commit","").lower()!=expected_sha.lower():
        raise PermissionError("Signed release source commit does not match the exact CI job commit.")
    return manifest


def _run_signed_deployment(job: dict, workflow: dict, workspace: Path) -> dict:
    deployment=workflow.get("deployment")
    if not isinstance(deployment,dict):
        raise PermissionError("No signed deployment configuration exists.")
    bundle=_workspace_file(workspace,deployment["bundle"],MAX_RELEASE_BUNDLE_BYTES)
    signature=_workspace_file(workspace,deployment["signature"],MAX_RELEASE_SIGNATURE_BYTES)
    _verify_release_bundle(bundle,deployment["application"],job["sha"])
    digest=hashlib.sha256(bundle.read_bytes()).hexdigest()
    from release_manager import Releases
    manager=Releases()
    staged=manager.stage(deployment["application"],bundle,signature,deployment["signer"],digest,confirm=True)
    deployed=manager.deploy(deployment["application"],digest,confirm=True)
    return {"application":deployment["application"],"bundle_sha256":digest,"stage":staged,
            "deployment":deployed,"independent_attestation":False}


def _job_log(job_id: str) -> Path:
    _private_dir(LOG_ROOT)
    path=LOG_ROOT/f"{job_id}.log"
    if path.is_symlink():
        raise PermissionError("Symlinked job log is forbidden.")
    return path


def _append_log(path: Path, message: str) -> None:
    if path.is_symlink():
        raise PermissionError("Symlinked job log is forbidden.")
    payload=(_safe_redact(message.rstrip())+"\n").encode("utf-8","replace")
    current=path.stat().st_size if path.exists() else 0
    if current >= MAX_OUTPUT_BYTES:
        return
    with path.open("ab") as stream:
        stream.write(payload[:MAX_OUTPUT_BYTES-current])
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path,0o600)


def execute_one(queue: Queue, job: dict) -> dict:
    config=read_config()
    started=time.monotonic()
    workspace=WORK_ROOT/job["id"]
    log_path=_job_log(job["id"])
    evidence={"job_id":job["id"],"provider":job["provider"],"repository":job["repo"],
              "event":job["event"],"ref":job["ref"],"commit_sha":job["sha"],
              "started_at":utc_iso(),"steps":[],"independent_attestation":False}
    try:
        if job.get("cancel_requested"):
            cancelled=queue.finish(job["id"],"cancelled",summary="Cancelled before execution.")
            try:_provider_status(cancelled,"cancelled","Open CI Hub job cancelled")
            except Exception:pass
            return cancelled
        if job.get("evidence_json"):
            try:
                stored_evidence=json.loads(job["evidence_json"])
                if isinstance(stored_evidence,dict):
                    evidence=stored_evidence
            except (ValueError,TypeError):
                raise RuntimeError("Stored execution evidence is invalid; refusing to continue.")
        if not job.get("workflow_json"):
            workflow_sha, workflow_raw = _checkout(job,workspace,workflow_sha=job.get("base_ref_sha",""))
            workflow=json.loads(workflow_raw)
            queue.update_workflow(job["id"],workflow,workflow_sha,workspace,log_path)
            job=queue.get(job["id"]) or job
        else:
            workflow=json.loads(job["workflow_json"])
            workspace=Path(job["workspace"])
            log_path=Path(job["log_path"])
        admission=_resource_admission(config,workflow)
        evidence["resource_admission"]=admission
        image_id=job.get("image_id") or _image_id(workflow["image"])
        queue.update_image_id(job["id"],image_id)
        cache_path=_cache_dir(job,image_id)
        evidence["cache_enabled"]=cache_path is not None
        evidence["image"]=workflow["image"]
        evidence["image_id"]=image_id
        index=queue.step_index(job["id"])
        total_deadline=started+min(config["max_job_seconds"],workflow["timeout_seconds"])
        for position in range(index,len(workflow["steps"])):
            if queue.is_cancelled(job["id"]):
                result=queue.finish(job["id"],"cancelled",summary="Cancellation acknowledged between steps.",
                                    duration=time.monotonic()-started,evidence=evidence)
                _provider_status(result,"cancelled","Open CI Hub job cancelled")
                return result
            step=workflow["steps"][position]
            remaining=int(total_deadline-time.monotonic())
            if remaining<1:
                raise TimeoutError("Overall workflow timeout exceeded.")
            timeout=min(step["timeout_seconds"],remaining)
            _append_log(log_path,f"--- Step {position+1}/{len(workflow['steps'])}: {step['name']} ({step['stage']}) ---")
            argv=_container_argv(job,workflow,image_id,step,workspace,cache_path)
            result=_run_bounded(argv,cwd=ROOT,timeout=timeout,job_id=job["id"],
                                should_cancel=lambda:("cancelled" if queue.is_cancelled(job["id"]) else _resource_abort_reason(config)))
            _append_log(log_path,result["output"])
            step_evidence={"name":step["name"],"stage":step["stage"],"exit_code":result["exit_code"],
                           "timed_out":result["timed_out"],"cancelled":result.get("cancelled",False),
                           "resource_abort":result.get("resource_abort",False),
                           "duration_seconds":result["duration_seconds"],
                           "log_sha256":hashlib.sha256(result["output"].encode()).hexdigest()}
            evidence["steps"].append(step_evidence)
            queue.set_step_index(job["id"],position+1)
            if result.get("cancelled") or queue.is_cancelled(job["id"]):
                cancelled=queue.finish(job["id"],"cancelled",summary="Cancellation acknowledged while a step was running.",
                                       duration=time.monotonic()-started,evidence=evidence)
                try:_provider_status(cancelled,"cancelled","Open CI Hub job cancelled")
                except Exception:pass
                return cancelled
            if result.get("resource_abort"):
                raise RuntimeError("Live resource guard stopped the job to preserve the configured RAM, disk and thermal reserves.")
            if result["timed_out"]:
                raise TimeoutError(f"Step {step['name']} timed out.")
            if result["exit_code"]!=0:
                raise RuntimeError(f"Step {step['name']} failed with exit code {result['exit_code']}.")
        if job.get("artifact_path") and Path(job["artifact_path"]).is_file() and not Path(job["artifact_path"]).is_symlink():
            artifact_path,artifact_sha=job["artifact_path"],job.get("artifact_sha256","")
        else:
            artifact_path,artifact_sha=_collect_artifacts(workspace,workflow["artifacts"],job["id"])
        evidence["artifact_sha256"]=artifact_sha
        if workflow.get("deployment") and not (queue.get(job["id"]) or {}).get("deployment_approved"):
            queue.set_step_index(job["id"],len(workflow["steps"]))
            app=workflow["deployment"]["application"]
            waiting=queue.finish(job["id"],"awaiting_approval",
                summary=f"Build passed; signed deployment to {app} awaits an offline signature and explicit local approval.",
                duration=time.monotonic()-started,evidence=evidence,
                artifact_path=artifact_path,artifact_sha256=artifact_sha)
            _provider_status(waiting,"awaiting_approval",f"Build passed; signed deploy to {app} awaits approval")
            return waiting
        if workflow.get("deployment"):
            queue.mark_deployment_started(job["id"])
            _append_log(log_path,f"--- Signed release deployment: {workflow['deployment']['application']} ---")
            deployment_result=_run_signed_deployment(job,workflow,workspace)
            evidence["deployment"]=deployment_result
            _append_log(log_path,"Signed release staged, deployed and health-checked.")
        evidence["finished_at"]=utc_iso()
        evidence["duration_seconds"]=round(time.monotonic()-started,3)
        evidence["evidence_sha256"]=hashlib.sha256(json.dumps(evidence,sort_keys=True).encode()).hexdigest()
        summary="All configured local workflow steps passed."
        if workflow.get("deployment"):
            summary+=f" Signed deployment to {workflow['deployment']['application']} passed health checks."
        passed=queue.finish(job["id"],"passed",summary=summary,
                            duration=time.monotonic()-started,evidence=evidence,
                            artifact_path=artifact_path,artifact_sha256=artifact_sha)
        _provider_status(passed,"passed",f"Open CI Hub passed · {job['id'][:8]}")
        return passed
    except ResourceDeferred as exc:
        message=f"{type(exc).__name__}: {str(exc)}"
        _append_log(log_path,message)
        evidence["resource_deferred"]={"reason":str(exc)[:500],"retry_after_seconds":RESOURCE_RETRY_SECONDS}
        deferred=queue.defer(job["id"],message,evidence=evidence)
        try:_provider_status(deferred,"pending",f"Open CI Hub deferred · retry in {RESOURCE_RETRY_SECONDS}s")
        except Exception:pass
        return deferred
    except Exception as exc:
        message=f"{type(exc).__name__}: {str(exc)}"
        _append_log(log_path,message)
        state="cancelled" if queue.is_cancelled(job["id"]) else "failed"
        evidence["finished_at"]=utc_iso()
        evidence["duration_seconds"]=round(time.monotonic()-started,3)
        evidence["error_class"]=type(exc).__name__
        failed=queue.finish(job["id"],state,error=message,summary="Local workflow did not complete successfully.",
                            duration=time.monotonic()-started,evidence=evidence)
        try:
            _provider_status(failed,state,f"Open CI Hub {state} · {job['id'][:8]}")
        except Exception:
            pass
        return failed


def _authorised_event(provider: str, event_name: str, payload: dict, queue: Queue, delivery_id: str,
                      body_sha: str) -> dict:
    if queue.delivery_seen(provider,delivery_id):
        return {"accepted":True,"duplicate":True,"delivery_id":delivery_id}
    normalised, reason=_normalise_event(provider,event_name,payload)
    if not normalised:
        inserted=queue.record_delivery(provider,delivery_id,body_sha,"ignored:"+reason)
        return {"accepted":True,"duplicate":not inserted,"ignored":reason,"delivery_id":delivery_id}
    try:
        repo_cfg=_repo_config(provider,normalised["repo"])
        branch=normalised["base_ref"] if normalised["event"] in ("pull_request","merge_request") else normalised["ref"]
        if not _branch_allowed(repo_cfg,branch):
            raise PermissionError("Branch/target is not allowlisted.")
        if repo_cfg["require_protected_branch"] and not _protected_branch(provider,normalised["repo"],branch):
            raise PermissionError("Branch/target is not confirmed protected by the provider.")
        job,created=queue.enqueue(**normalised,delivery_id=delivery_id)
    except (ValueError,PermissionError,RuntimeError) as exc:
        inserted=queue.record_delivery(provider,delivery_id,body_sha,"rejected:"+str(exc)[:200])
        return {"accepted":False,"duplicate":not inserted,"rejected":str(exc)[:200],"delivery_id":delivery_id}
    inserted=queue.record_delivery(provider,delivery_id,body_sha,"queued" if created else "deduplicated",job["id"])
    if not inserted:
        return {"accepted":True,"duplicate":True,"delivery_id":delivery_id}
    try:
        _provider_status(job,"pending",f"Open CI Hub queued · {job['id'][:8]}")
    except Exception:
        pass
    return {"accepted":True,"duplicate":False,"job_id":job["id"],"state":job["state"],"created":created}


def _auth_header(handler, token: str) -> bool:
    supplied=handler.headers.get("Authorization","")
    if not supplied.startswith("Bearer "):
        return False
    return hmac.compare_digest(supplied[7:],token)


DASHBOARD = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Open CI Hub — Local execution</title>
<style>
:root{color-scheme:dark;font:14px/1.5 system-ui,sans-serif;background:#101318;color:#eef1f6}
body{max-width:1200px;margin:2rem auto;padding:0 1rem}h1{font-size:1.5rem}p{color:#b8c0ce}
input,button{font:inherit;border:1px solid #454d5a;border-radius:8px;padding:.65rem;background:#1c222b;color:inherit}
button{cursor:pointer}button:focus-visible,input:focus-visible{outline:2px solid #e0b85c;outline-offset:2px}
header,.toolbar{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center}.toolbar input{flex:1;min-width:16rem}
.card{background:#191e26;border:1px solid #303845;border-radius:12px;padding:1rem;margin:1rem 0;overflow:auto}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:.6rem;border-bottom:1px solid #303845;vertical-align:top}
code{overflow-wrap:anywhere}.state{font-weight:700}.muted{color:#aab3c2}.actions{display:flex;gap:.35rem;flex-wrap:wrap}
button.danger{border-color:#9d5555}button.approve{border-color:#ad8737}
@media(max-width:720px){body{margin:1rem auto}table,tbody,tr,td{display:block}thead{display:none}td{border-bottom:0;padding:.25rem .6rem}tr{border-bottom:1px solid #303845;padding:.5rem 0}.actions{margin:.4rem 0}}
</style></head><body>
<header><div><h1>Open CI Hub — Local execution</h1><p>Local-first job queue · provider-reported results · no hosted build runner</p></div></header>
<div class="card"><div class="toolbar"><input id="token" type="password" autocomplete="current-password" aria-label="Local owner token" placeholder="Paste the local owner token"><button id="refresh">Refresh</button><button id="forget">Forget token</button></div><p id="message" role="status" aria-live="polite">Enter the owner token printed by the local initialisation command.</p></div>
<div class="card"><h2>Queue status</h2><pre id="status" class="muted">Not loaded.</pre></div>
<div class="card"><h2>Recent jobs</h2><table><thead><tr><th>Job / repository</th><th>Commit / ref</th><th>Deployment</th><th>State</th><th>Updated</th><th>Actions</th></tr></thead><tbody id="jobs"></tbody></table></div>
<script>
const token=document.getElementById('token'),message=document.getElementById('message');
function text(el,value){el.textContent=value==null?'':String(value)}
async function api(path,method='GET'){const headers={'Authorization':'Bearer '+token.value};if(method!=='GET'){headers['X-Open-CI-Request']='1';headers['Content-Type']='application/json'}const r=await fetch(path,{method,headers,body:method==='GET'?undefined:'{}',credentials:'same-origin'});if(!r.ok)throw new Error('Hub returned HTTP '+r.status);return r.json()}
function actionButton(label,job,action,klass=''){const b=document.createElement('button');b.textContent=label;b.className=klass;b.addEventListener('click',async()=>{if(!confirm(label+' job '+job.id+'?'))return;try{await api('/api/jobs/'+job.id+'/'+action,'POST');await refresh()}catch(e){text(message,e.message)}});return b}
async function downloadArtifact(job){try{const r=await fetch('/api/jobs/'+job.id+'/artifact',{headers:{'Authorization':'Bearer '+token.value}});if(!r.ok)throw new Error('Artifact download failed (HTTP '+r.status+').');const blob=await r.blob();const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=job.id+'.tar.gz';document.body.append(a);a.click();a.remove();URL.revokeObjectURL(url)}catch(e){text(message,e.message)}}
async function refresh(){if(!token.value){text(message,'Enter the local owner token first.');return}try{const s=await api('/api/status');const j=await api('/api/jobs');text(document.getElementById('status'),JSON.stringify(s,null,2));const body=document.getElementById('jobs');body.replaceChildren();for(const job of j.jobs){const tr=document.createElement('tr');const cells=[];for(const value of [job.id+'\\n'+job.provider+'/'+job.repo,job.sha+'\\n'+job.ref,job.deployment_app?'Signed deploy: '+job.deployment_app:'—',job.state,job.updated_at?new Date(job.updated_at*1000).toLocaleString():'']){const td=document.createElement('td');text(td,value);tr.append(td)}const td=document.createElement('td');td.className='actions';if(job.artifact_sha256){const download=document.createElement('button');download.textContent='Download artifact';download.addEventListener('click',()=>downloadArtifact(job));td.append(download)}if(['failed','interrupted'].includes(job.state))td.append(actionButton('Retry',job,'retry'));if(['pending','running','awaiting_approval'].includes(job.state))td.append(actionButton('Cancel',job,'cancel','danger'));if(job.state==='awaiting_approval')td.append(actionButton('Approve signed deploy',job,'approve','approve'));tr.append(td);body.append(tr)}text(message,'Live status loaded. Logs and artifacts are available through authenticated local API endpoints.')}catch(e){text(message,e.message)}}
document.getElementById('refresh').addEventListener('click',refresh);document.getElementById('forget').addEventListener('click',()=>{token.value='';text(message,'Owner token cleared from this page.')});</script></body></html>"""


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, handler, queue: Queue):
        self.queue = queue
        super().__init__(address,handler)


def make_handler(queue: Queue):
    class Handler(BaseHTTPRequestHandler):
        server_version = "OpenCIHubBridge/1"
        sys_version = ""

        def log_message(self, format, *args):
            # Never write webhook headers, payloads, tokens or query strings to stdout.
            return

        def _json(self, status: int, value: dict):
            body=json.dumps(value,sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Content-Length",str(len(body)))
            self.send_header("Cache-Control","no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> bytes:
            length=self.headers.get("Content-Length","")
            if not length.isdecimal() or int(length)<1 or int(length)>MAX_BODY:
                raise ValueError("Invalid or oversized request body.")
            return self.rfile.read(int(length))

        def _owner(self) -> bool:
            return _auth_header(self,read_secrets()["owner_token"])

        def do_GET(self):
            path=urlsplit(self.path).path
            if path=="/health":
                return self._json(200,{"ok":True,"service":"open-ci-hub-ci-execution-bridge"})
            if path=="/":
                body=DASHBOARD.encode("utf-8")
                self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8")
                self.send_header("Content-Length",str(len(body)));self.send_header("Cache-Control","no-store")
                self.end_headers();self.wfile.write(body);return
            if path.startswith("/api/"):
                try:
                    if not self._owner():return self._json(401,{"error":"Local owner token required."})
                    if path=="/api/status":return self._json(200,queue.status())
                    if path=="/api/jobs":return self._json(200,{"jobs":queue.jobs()})
                    match=re.fullmatch(r"/api/jobs/([0-9a-f]{32})/(log|artifact)",path)
                    if match:
                        job=queue.get(match.group(1))
                        if not job:return self._json(404,{"error":"Unknown job."})
                        if match.group(2)=="log":
                            log=Path(job["log_path"]) if job.get("log_path") else None
                            if not log or log.is_symlink() or not log.is_file():return self._json(404,{"error":"Job log is unavailable."})
                            payload=_safe_redact(log.read_text(encoding="utf-8",errors="replace"))[:MAX_OUTPUT_BYTES].encode()
                            self.send_response(200);self.send_header("Content-Type","text/plain; charset=utf-8")
                            self.send_header("Content-Length",str(len(payload)));self.send_header("Cache-Control","no-store")
                            self.end_headers();self.wfile.write(payload);return
                        artifact=Path(job["artifact_path"]) if job.get("artifact_path") else None
                        if not artifact or artifact.is_symlink() or not artifact.is_file() or not artifact.resolve().is_relative_to(ARTIFACT_ROOT.resolve()):
                            return self._json(404,{"error":"Job artifact is unavailable."})
                        if artifact.stat().st_size>MAX_ARTIFACT_BYTES+1024*1024:return self._json(413,{"error":"Artifact exceeds the download limit."})
                        payload=artifact.read_bytes()
                        self.send_response(200);self.send_header("Content-Type","application/gzip")
                        self.send_header("Content-Disposition",f'attachment; filename="{job["id"]}.tar.gz"')
                        self.send_header("Content-Length",str(len(payload)));self.send_header("Cache-Control","no-store")
                        self.end_headers();self.wfile.write(payload);return
                except (OSError,ValueError,RuntimeError,PermissionError) as exc:
                    return self._json(400,{"error":str(exc)[:200]})
            self._json(404,{"error":"Not found."})

        def do_POST(self):
            path=urlsplit(self.path).path
            provider = "github" if path=="/webhooks/github" else "gitlab" if path=="/webhooks/gitlab" else ""
            if provider:
                try:
                    raw=self._body()
                    secrets_data=read_secrets()
                    delivery=self.headers.get("X-GitHub-Delivery","") if provider=="github" else self.headers.get("X-Gitlab-Event-UUID","")
                    event=self.headers.get("X-GitHub-Event","") if provider=="github" else self.headers.get("X-Gitlab-Event","")
                    if not DELIVERY_RE.fullmatch(delivery):
                        return self._json(400,{"error":"Provider delivery ID is required for replay protection."})
                    secret=secrets_data["github_webhook_secret" if provider=="github" else "gitlab_webhook_secret"]
                    if not secret:
                        return self._json(503,{"error":"Webhook secret is not configured."})
                    if provider=="github":
                        supplied=self.headers.get("X-Hub-Signature-256","")
                        expected="sha256="+hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()
                        if not hmac.compare_digest(supplied,expected):
                            return self._json(401,{"error":"Webhook signature invalid."})
                    else:
                        supplied=self.headers.get("X-Gitlab-Token","")
                        if not hmac.compare_digest(supplied,secret):
                            return self._json(401,{"error":"Webhook token invalid."})
                    body_sha=hashlib.sha256(raw).hexdigest()
                    if queue.delivery_seen(provider,delivery):
                        return self._json(202,{"accepted":True,"duplicate":True,"delivery_id":delivery})
                    try:
                        payload=json.loads(raw)
                    except (ValueError,UnicodeError):
                        return self._json(400,{"error":"Invalid JSON payload."})
                    if not isinstance(payload,dict):
                        return self._json(400,{"error":"JSON payload must be an object."})
                    result=_authorised_event(provider,event,payload,queue,delivery,body_sha)
                    return self._json(202 if result.get("accepted") else 403,result)
                except (OSError,ValueError,RuntimeError,PermissionError) as exc:
                    return self._json(400,{"error":str(exc)[:200]})
            if path.startswith("/api/jobs/"):
                try:
                    if not self._owner():return self._json(401,{"error":"Local owner token required."})
                    origin=self.headers.get("Origin","")
                    parsed=urlsplit(origin)
                    if self.headers.get("X-Open-CI-Request")!="1" or parsed.scheme!="http" or parsed.hostname not in ("127.0.0.1","localhost") or parsed.port!=8766:
                        return self._json(403,{"error":"Same-origin local owner request required."})
                    match=re.fullmatch(r"/api/jobs/([0-9a-f]{32})/(retry|cancel|approve)",path)
                    if not match:return self._json(404,{"error":"Not found."})
                    self._body()
                    job_id,action=match.groups()
                    if action=="retry":result=queue.retry(job_id,confirm=True)
                    elif action=="cancel":result=queue.cancel(job_id,confirm=True)
                    else:result=queue.approve(job_id,confirm=True)
                    try:_provider_status(result,result["state"],result.get("summary") or result["state"])
                    except Exception:pass
                    return self._json(200,{"job_id":job_id,"state":result["state"]})
                except (OSError,ValueError,RuntimeError,PermissionError) as exc:
                    return self._json(400,{"error":str(exc)[:200]})
            self._json(404,{"error":"Not found."})

    return Handler


def poll_once(queue: Queue) -> dict:
    config=read_config()
    summary={"checked":0,"queued":0,"errors":[]}
    for repo_cfg in config["repos"]:
        provider,repo=repo_cfg["provider"],repo_cfg["repo"]
        try:
            encoded=quote(repo,safe="")
            if provider=="github":
                branches=invoke_json(["gh","api",f"repos/{repo}/branches?per_page=100"])
                if not isinstance(branches,list):raise RuntimeError("Unexpected GitHub branches response.")
                for branch in branches:
                    if not isinstance(branch,dict) or not isinstance(branch.get("name"),str):continue
                    name=branch["name"]
                    if not _branch_allowed(repo_cfg,name):continue
                    commit=branch.get("commit",{})
                    sha=commit.get("sha") if isinstance(commit,dict) else None
                    if not isinstance(sha,str) or not SHA_RE.fullmatch(sha):continue
                    summary["checked"]+=1
                    previous=queue.get_ref(provider,repo,name)
                    if previous==sha:continue
                    if repo_cfg["require_protected_branch"] and branch.get("protected") is not True:
                        queue.note_poll(provider,repo,"skipped",f"{name}: branch not protected")
                        continue
                    job,created=queue.enqueue(provider=provider,repo=repo,event="poll",action="branch-head",
                                              ref=name,sha=sha,source_repo=repo,actor="poller")
                    queue.set_ref(provider,repo,name,sha)
                    if created:
                        summary["queued"]+=1
                        try:_provider_status(job,"pending",f"Open CI Hub queued · {job['id'][:8]}")
                        except Exception:pass
                if repo_cfg["allow_pull_requests"]:
                    pulls=invoke_json(["gh","api",f"repos/{repo}/pulls?state=open&per_page=100"])
                    if not isinstance(pulls,list):raise RuntimeError("Unexpected GitHub pull-request response.")
                    for pr in pulls:
                        if not isinstance(pr,dict):continue
                        head,base=pr.get("head"),pr.get("base")
                        if not isinstance(head,dict) or not isinstance(base,dict):continue
                        source=head.get("repo")
                        if not isinstance(source,dict) or source.get("full_name","").lower()!=repo.lower():continue
                        base_ref,sha=head.get("ref"),head.get("sha")
                        target=base.get("ref")
                        if not isinstance(base_ref,str) or not isinstance(target,str) or not isinstance(sha,str) or not SHA_RE.fullmatch(sha):continue
                        if not _branch_allowed(repo_cfg,target):continue
                        if repo_cfg["require_protected_branch"] and not _protected_branch(provider,repo,target):continue
                        job,created=queue.enqueue(provider=provider,repo=repo,event="pull_request",action="poll",
                                                  ref=base_ref,sha=sha,base_ref=target,source_repo=repo,actor="poller")
                        if created:
                            summary["queued"]+=1
                            try:_provider_status(job,"pending",f"Open CI Hub queued · {job['id'][:8]}")
                            except Exception:pass
            else:
                branches=invoke_json(["glab","api",f"projects/{encoded}/repository/branches?per_page=100"])
                if not isinstance(branches,list):raise RuntimeError("Unexpected GitLab branches response.")
                for branch in branches:
                    if not isinstance(branch,dict) or not isinstance(branch.get("name"),str):continue
                    name=branch["name"]
                    if not _branch_allowed(repo_cfg,name):continue
                    commit=branch.get("commit",{})
                    sha=commit.get("id") if isinstance(commit,dict) else None
                    if not isinstance(sha,str) or not SHA_RE.fullmatch(sha):continue
                    summary["checked"]+=1
                    previous=queue.get_ref(provider,repo,name)
                    if previous==sha:continue
                    if repo_cfg["require_protected_branch"] and branch.get("protected") is not True:
                        queue.note_poll(provider,repo,"skipped",f"{name}: branch not protected")
                        continue
                    job,created=queue.enqueue(provider=provider,repo=repo,event="poll",action="branch-head",
                                              ref=name,sha=sha,source_repo=repo,actor="poller")
                    queue.set_ref(provider,repo,name,sha)
                    if created:
                        summary["queued"]+=1
                        try:_provider_status(job,"pending",f"Open CI Hub queued · {job['id'][:8]}")
                        except Exception:pass
                if repo_cfg["allow_pull_requests"]:
                    mrs=invoke_json(["glab","api",f"projects/{encoded}/merge_requests?state=opened&per_page=100"])
                    if not isinstance(mrs,list):raise RuntimeError("Unexpected GitLab merge-request response.")
                    for mr in mrs:
                        if not isinstance(mr,dict):continue
                        if type(mr.get("source_project_id")) is not int or type(mr.get("target_project_id")) is not int or mr.get("source_project_id") != mr.get("target_project_id"):continue
                        source=mr.get("source_branch");target=mr.get("target_branch")
                        sha=mr.get("sha") or (mr.get("diff_refs") or {}).get("head_sha")
                        if not isinstance(source,str) or not isinstance(target,str) or not isinstance(sha,str) or not SHA_RE.fullmatch(sha):continue
                        if not _branch_allowed(repo_cfg,target):continue
                        if repo_cfg["require_protected_branch"] and not _protected_branch(provider,repo,target):continue
                        job,created=queue.enqueue(provider=provider,repo=repo,event="merge_request",action="poll",
                                                  ref=source,sha=sha,base_ref=target,source_repo=repo,actor="poller")
                        if created:
                            summary["queued"]+=1
                            try:_provider_status(job,"pending",f"Open CI Hub queued · {job['id'][:8]}")
                            except Exception:pass
            queue.note_poll(provider,repo,"success",f"checked={summary['checked']} queued={summary['queued']}")
        except Exception as exc:
            summary["errors"].append({"provider":provider,"repo":repo,"error":f"{type(exc).__name__}: {str(exc)[:160]}"})
            queue.note_poll(provider,repo,"error",f"{type(exc).__name__}: {str(exc)[:160]}")
    return summary


def worker_loop(queue: Queue) -> None:
    while not STOP.is_set():
        try:
            config=read_config()
            job=queue.claim(config["max_concurrent"])
            if not job:
                STOP.wait(2)
                continue
            try:_provider_status(job,"running",f"Open CI Hub running · {job['id'][:8]}")
            except Exception:pass
            result=execute_one(queue,job)
            if result.get("state") not in ("pending","awaiting_approval","passed","failed","cancelled","blocked","interrupted"):
                queue.finish(job["id"],"failed",error="Worker returned an unexpected state.",summary="Execution bridge internal error.")
        except Exception:
            STOP.wait(3)


def poll_loop(queue: Queue, interval: int) -> None:
    while not STOP.is_set():
        try:poll_once(queue)
        except Exception:pass
        STOP.wait(interval)


def _service_lock():
    _private_dir(LOCAL)
    lock_path=LOCAL/"ci-bridge-service.lock"
    if lock_path.is_symlink():
        raise PermissionError("Symlinked service lock is forbidden.")
    import fcntl
    handle=lock_path.open("a+")
    os.chmod(lock_path,0o600)
    try:
        fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError("Another CI execution bridge service already holds the local lock.") from None
    return handle


def serve(host: str = "127.0.0.1", port: int = 8766, poll_seconds: int | None = None) -> None:
    require_owner()
    if host not in ("127.0.0.1","::1"):
        raise PermissionError("The CI execution bridge must bind to loopback; use a separately authenticated reverse proxy for webhooks.")
    if type(port) is not int or not 1024<=port<=65535:
        raise ValueError("Port must be 1024–65535.")
    config=read_config()
    queue=Queue()
    lock=_service_lock()
    recovered=queue.recover_interrupted()
    if recovered:
        print(f"Recovered {recovered} running job(s) as interrupted; they require manual inspection/retry.",file=sys.stderr)
    worker=threading.Thread(target=worker_loop,args=(queue,),daemon=True,name="ci-bridge-worker")
    worker.start()
    interval=poll_seconds if poll_seconds is not None else config["poll_seconds"]
    poller=threading.Thread(target=poll_loop,args=(queue,interval),daemon=True,name="ci-bridge-poller")
    poller.start()
    server=BridgeServer((host,port),make_handler(queue),queue)
    print(f"Open CI Hub execution bridge listening on {host}:{port}; max_concurrent={config['max_concurrent']}; poll={interval}s")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        STOP.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        poller.join(timeout=2)
        lock.close()


def cli_enqueue(args) -> dict:
    require_owner()
    if not args.confirm:
        raise PermissionError("Manual local enqueue requires --confirm.")
    repo_cfg=_repo_config(args.provider,args.repo)
    if repo_cfg["require_protected_branch"] and not _protected_branch(args.provider,args.repo,args.ref):
        raise PermissionError("Manual execution is limited to provider-confirmed protected branches by local policy.")
    queue=Queue()
    job,created=queue.enqueue(provider=args.provider,repo=args.repo,event="manual",action="manual",
                              ref=args.ref,sha=args.sha,source_repo=args.repo,
                              actor=os.environ.get("USER","local-owner")[:100],manual_nonce=secrets.token_hex(8))
    if created:
        try:_provider_status(job,"pending",f"Open CI Hub manual job queued · {job['id'][:8]}")
        except Exception:pass
    return {"job_id":job["id"],"created":created,"state":job["state"],"sha":job["sha"]}


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description="Open CI Hub local execution bridge")
    sub=parser.add_subparsers(dest="command",required=True)
    init_parser=sub.add_parser("init");init_parser.add_argument("--confirm",action="store_true")
    allow=sub.add_parser("allow-repo");allow.add_argument("--provider",choices=("github","gitlab"),required=True)
    allow.add_argument("--repo",required=True);allow.add_argument("--branches",default="main")
    allow.add_argument("--allow-pull-requests",action="store_true");allow.add_argument("--allow-network",action="store_true")
    allow.add_argument("--allow-unprotected",action="store_true")
    allow.add_argument("--images",default="python:3.12-slim",help="Comma-separated exact local image names approved for this repository")
    allow.add_argument("--confirm",action="store_true")
    secret=sub.add_parser("set-webhook-secret");secret.add_argument("--provider",choices=("github","gitlab"),required=True)
    secret.add_argument("--confirm",action="store_true")
    manual=sub.add_parser("enqueue");manual.add_argument("--provider",choices=("github","gitlab"),required=True)
    manual.add_argument("--repo",required=True);manual.add_argument("--ref",required=True);manual.add_argument("--sha",required=True)
    manual.add_argument("--confirm",action="store_true")
    sig=sub.add_parser("attach-signature");sig.add_argument("job_id");sig.add_argument("source_path");sig.add_argument("--confirm",action="store_true")
    for name in ("retry","cancel","approve"):
        item=sub.add_parser(name);item.add_argument("job_id");item.add_argument("--confirm",action="store_true")
    status=sub.add_parser("status");status.add_argument("--json",action="store_true")
    poll=sub.add_parser("poll");poll.add_argument("--once",action="store_true")
    srv=sub.add_parser("serve");srv.add_argument("--host",default="127.0.0.1");srv.add_argument("--port",type=int,default=8766)
    srv.add_argument("--poll-seconds",type=int)
    args=parser.parse_args(argv)
    try:
        require_owner()
        if args.command=="init":
            result=init_local(confirm=args.confirm)
        elif args.command=="allow-repo":
            branches=[x.strip() for x in args.branches.split(",") if x.strip()]
            images=[x.strip() for x in args.images.split(",") if x.strip()]
            result=add_repo(args.provider,args.repo,branches,allow_pull_requests=args.allow_pull_requests,
                            allow_network=args.allow_network,allow_unprotected=args.allow_unprotected,
                            allowed_images=images,confirm=args.confirm)
        elif args.command=="set-webhook-secret":
            if not args.confirm:
                raise PermissionError("Use set-webhook-secret --confirm as the local owner.")
            import getpass
            value=getpass.getpass(f"{args.provider.title()} webhook secret (hidden): ")
            result=set_webhook_secret(args.provider,value,confirm=args.confirm)
        elif args.command=="enqueue":
            result=cli_enqueue(args)
        elif args.command=="attach-signature":
            result=attach_signature(args.job_id,args.source_path,confirm=args.confirm)
        elif args.command in ("retry","cancel","approve"):
            queue=Queue()
            if args.command=="retry":result=queue.retry(args.job_id,confirm=args.confirm)
            elif args.command=="cancel":result=queue.cancel(args.job_id,confirm=args.confirm)
            else:result=queue.approve(args.job_id,confirm=args.confirm)
            try:_provider_status(result,result["state"],result.get("summary") or result["state"])
            except Exception:pass
        elif args.command=="status":
            result=Queue().status()
        elif args.command=="poll":
            result=poll_once(Queue())
        elif args.command=="serve":
            serve(args.host,args.port,args.poll_seconds)
            return 0
        else:
            raise ValueError("Unknown command.")
        print(json.dumps(result,sort_keys=True,indent=2))
        return 0
    except (OSError,ValueError,RuntimeError,PermissionError,FileExistsError,sqlite3.Error) as exc:
        print("Open CI Hub execution bridge error: "+str(exc)[:400],file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
