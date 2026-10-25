#!/usr/bin/env python3
"""Offline-key signing coordinator for verified GitHub/GitLab preview releases.

MUST run as a separate, trusted OS principal outside all CI runners.
Never execute this module inside GitHub/GitLab Actions. The external signer
re-queries provider APIs and signs exact output bytes; it never trusts a
runner-supplied "passed" JSON receipt.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import provider_artifact_verifier as verifier
import signed_preview_queue as queue

POLICY_SCHEMA = "open-ci-hub.attestor.v1"
MINUTES = 10
MAX_CONFIG_BYTES = 8192


def require_separate_principal():
    if os.name != "posix" or os.geteuid() == 0:
        raise PermissionError("Attestor requires dedicated non-root OS account.")
    if any(os.environ.get(k) for k in ("CI", "GITHUB_ACTIONS", "GITLAB_CI",
                                        "GITHUB_RUN_ID", "CI_PIPELINE_ID")):
        raise PermissionError("Signing is prohibited inside any CI job.")


def _owner_file(path, *, max_bytes, private_parent=True):
    p = Path(path)
    if not p.is_absolute() or p.is_symlink() or not p.is_file():
        raise PermissionError("Signing policy/key must be an existing absolute regular file.")
    st = p.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
        raise PermissionError("Signing policy/key must be owned by signer, mode 0600.")
    if private_parent:
        parent = p.parent
        ps = parent.stat()
        if parent.is_symlink() or ps.st_uid != os.geteuid() or ps.st_mode & 0o077:
            raise PermissionError("Signing secrets require a private directory.")
    if st.st_size <= 0 or st.st_size > max_bytes:
        raise PermissionError("Invalid signer file size.")
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        data = bytearray()
        while len(data) <= max_bytes:
            part = os.read(fd, min(4096, max_bytes + 1 - len(data)))
            if not part:
                break
            data.extend(part)
        if len(data) > max_bytes:
            raise PermissionError("Signing file exceeded maximum size.")
        return bytes(data)
    finally:
        os.close(fd)


def policy(path):
    require_separate_principal()
    obj = json.loads(_owner_file(path, max_bytes=MAX_CONFIG_BYTES))
    expected = {
        "schema", "issuer", "app", "repository", "provider",
        "trusted_actor", "ref_prefixes", "required_checks",
        "workflow_id", "artifact_job", "private_key_path",
        "signer_uid", "blocked_runner_uids"
    }
    if not isinstance(obj, dict) or set(obj) != expected or obj["schema"] != POLICY_SCHEMA:
        raise PermissionError("Invalid signer policy schema.")
    if type(obj["signer_uid"]) is not int or obj["signer_uid"] != os.geteuid():
        raise PermissionError("Signer OS identity does not match enrolled principal.")
    if (not isinstance(obj["blocked_runner_uids"], list)
        or not obj["blocked_runner_uids"]
        or any(type(uid) is not int for uid in obj["blocked_runner_uids"])
        or os.geteuid() in obj["blocked_runner_uids"]):
        raise PermissionError("Explicit separate CI runner UID allowlist required.")
    if (obj["provider"] not in ("github", "gitlab")
        or not isinstance(obj["app"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", obj["app"])
        or not isinstance(obj["repository"], str)
        or not verifier.REPO.fullmatch(obj["repository"])
        or not isinstance(obj["issuer"], str)
        or not obj["issuer"].startswith("https://")
        or not isinstance(obj["trusted_actor"], str)
        or not verifier.NAME.fullmatch(obj["trusted_actor"])):
        raise PermissionError("Invalid attestor repository/identity.")
    if (not isinstance(obj["ref_prefixes"], list) or not obj["ref_prefixes"]
        or len(obj["ref_prefixes"]) > 10
        or any(not isinstance(x, str) or not x.startswith("refs/heads/")
               or not x.endswith("/") or ".." in x or "//" in x
               or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-./"
                      for c in x) for x in obj["ref_prefixes"])):
        raise PermissionError("Invalid preview branch allowlist.")
    try:
        verifier._inputs(obj["repository"], "1", "a"*40,
                         "refs/heads/feature/preflight",
                         obj["trusted_actor"], obj["required_checks"])
    except PermissionError:
        raise PermissionError("Invalid required attestation checks.") from None
    if (obj["provider"] == "github" and
        (type(obj["workflow_id"]) is not int or obj["workflow_id"] <= 0
         or obj["artifact_job"] is not None)):
        raise PermissionError("GitHub signer must pin one workflow ID.")
    if (obj["provider"] == "gitlab" and
        (obj["workflow_id"] is not None
         or not isinstance(obj["artifact_job"], str)
         or not verifier.NAME.fullmatch(obj["artifact_job"]))):
        raise PermissionError("GitLab signer must pin one artifact job.")
    key_path = Path(obj["private_key_path"])
    checkout = Path(__file__).resolve().parent
    if (not key_path.is_absolute() or key_path == checkout
        or checkout in key_path.parents):
        raise PermissionError("Never store the signer private key in the project checkout.")
    return obj


def _sign(payload, key_path):
    key = serialization.load_pem_private_key(
        _owner_file(key_path, max_bytes=8192), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise PermissionError("Signer key must be private Ed25519.")
    return base64.urlsafe_b64encode(key.sign(queue.canonical(payload))).decode().rstrip("=")


def verify_and_sign(config, *, run_id, sha, ref,
                    github_verify=verifier.verify_github,
                    gitlab_verify=verifier.verify_gitlab,
                    now=None):
    require_separate_principal()
    # Enforce exact locally enrolled branch prefix before any external fetch.
    if (not isinstance(ref, str)
        or not any(ref.startswith(prefix) and len(ref) > len(prefix)
                   for prefix in config["ref_prefixes"])
        or ".." in ref or "//" in ref):
        raise PermissionError("Branch outside attestor scope.")
    repo = config["repository"]
    actor = config["trusted_actor"]
    checks = config["required_checks"]
    if config["provider"] == "github":
        rec, zip_bytes = github_verify(repo, run_id, sha, ref, actor, checks,
                                       workflow_id=config["workflow_id"])
    else:
        rec, zip_bytes = gitlab_verify(repo, run_id, sha, ref, actor, checks,
                                       artifact_job=config["artifact_job"])
    if (rec.get("provider") != config["provider"]
        or rec.get("repository") != repo or rec.get("ref") != ref
        or rec.get("run_id") != run_id or rec.get("commit") != sha
        or rec.get("checked_out_sha") != sha
        or rec.get("run_status") != "passed"
        or rec.get("artifact_sha256") != hashlib.sha256(zip_bytes).hexdigest()
        or rec.get("actor", "").casefold() != actor.casefold()
        or rec.get("independently_attested") is not False
        or set(rec.get("checks", {})) != set(checks)):
        raise PermissionError("Provider verifier returned inconsistent evidence.")
    # Fresh proof scoped to exact already-verified bytes and user-enrolled preview.
    epoch = int(time.time() if now is None else now)
    proof = {
        "schema": queue.SCHEMA, "issuer": config["issuer"],
        "app": config["app"], "repository": repo, "ref": ref,
        "commit": sha, "checked_out_sha": sha,
        "artifact_sha256": rec["artifact_sha256"], "run_id": run_id,
        "run_status": "passed", "checks": rec["checks"],
        "issued_at": epoch, "expires_at": epoch + MINUTES * 60,
        "nonce": base64.urlsafe_b64encode(secrets.token_bytes(24)).decode().rstrip("="),
        "environment": "preview"
    }
    signed = {"payload": proof, "signature": _sign(proof, config["private_key_path"])}
    return signed, zip_bytes


def handoff(directory, signed, artifact):
    """Atomically staged private output. Transport to PC remains separate."""
    dest = Path(directory)
    if not dest.is_absolute() or dest.is_symlink() or not dest.is_dir():
        raise PermissionError("Delivery directory must exist and be absolute.")
    st = dest.stat()
    if st.st_uid != os.geteuid() or st.st_mode & 0o077:
        raise PermissionError("Delivery directory must be signer-owned 0700.")
    nonce = signed["payload"]["nonce"]
    if not queue.NONCE.fullmatch(nonce):
        raise PermissionError("Invalid output identity.")
    base = "signed-preview-" + nonce
    paths = [(dest / (base + ".zip"), artifact),
             (dest / (base + ".json"),
              json.dumps(signed, sort_keys=True).encode("utf-8"))]
    if any(p.exists() or p.is_symlink() for p, _ in paths):
        raise PermissionError("Refusing to overwrite signed release delivery.")
    written = []
    try:
        for p, data in paths:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            written.append(p)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
    except BaseException:
        for p in written:
            p.unlink(missing_ok=True)
        raise
    return {"status": "signed_waiting_for_authenticated_delivery",
            "manifest": str(paths[1][0]), "release_zip": str(paths[0][0]),
            "sha256": signed["payload"]["artifact_sha256"]}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--policy", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--ref", required=True)
    p.add_argument("--output-dir", required=True)
    a = p.parse_args(argv)
    try:
        settings = policy(a.policy)
        signed, release = verify_and_sign(settings, run_id=a.run_id, sha=a.sha,
                                           ref=a.ref)
        print(json.dumps(handoff(a.output_dir, signed, release), indent=2))
        return 0
    except (PermissionError, OSError, ValueError, TypeError) as error:
        print("Attestation rejected: " + str(error)[:220], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
