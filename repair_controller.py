#!/usr/bin/env python3
"""Fail-closed candidate patch and merge gate for Open CI Hub.

This module never executes a coding model and never merges by itself. It validates
an externally generated unified diff and evaluates independently sourced merge
evidence. The caller must authenticate evidence and execute tests in a disposable
network-isolated worker before using a positive decision.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
from pathlib import PurePosixPath
import re
import sys
from pathlib import Path

SCHEMA = "open-ci-hub.repair-gate.v1"
MAX_PATCH_BYTES = 1_000_000
MAX_FILES = 40
MAX_LINES = 20_000
SHA = re.compile(r"^[a-f0-9]{40}$")
SAFE_PATH = re.compile(r"^[A-Za-z0-9_./@+-]{1,240}$")
PROTECTED_PARTS = {
    ".git", ".github", ".gitlab", ".env", "secrets", "credentials",
    "auth", "authentication", "authorization", "crypto", "cryptography",
    "migrations", "deploy", "deployment", "infra", "terraform",
}
PROTECTED_NAMES = {
    "release_policy.py", "repair_controller.py", "autonomy_sandbox.py",
    "auth_broker.py", "browser_session_vault.py", "ci_bridge.py",
    "ci_bridge_poll.py", "release_policy.py", "release_manager.py",
    "autonomy_core.py", "autonomy_attestation.py", "zero_cost_dispatch.py",
    "machine_policy.py", "managed_worker.py", "scripts/install-autonomy-service.sh",
}
REQUIRED_EVIDENCE = {
    "schema", "repository", "base_sha", "candidate_sha", "pr_number",
    "trusted_actor", "risk", "required_checks", "checks", "review",
    "branch_protection", "merge_state", "source_isolated", "patch_paths",
}


class GateError(ValueError):
    """Candidate or evidence violates the repair/merge contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError(message)


def _path_ok(path: str, allow_patterns: list[str]) -> bool:
    if not isinstance(path, str) or not SAFE_PATH.fullmatch(path):
        return False
    p = PurePosixPath(path)
    if p.is_absolute() or any(part in ("", ".", "..") for part in path.split("/")):
        return False
    lower_parts = {part.casefold() for part in p.parts}
    if lower_parts & PROTECTED_PARTS or path in PROTECTED_NAMES:
        return False
    if path.startswith(".github/workflows/") or path.startswith(".gitlab/"):
        return False
    if path.endswith((".pem", ".key", ".p12", ".pfx", ".sqlite", ".sqlite3")):
        return False
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in allow_patterns)


def validate_patch(patch: str, *, allow_patterns: list[str]) -> dict:
    """Validate a textual git diff without applying it or trusting its contents."""
    if not isinstance(patch, str):
        raise GateError("Patch must be text.")
    raw = patch.encode("utf-8", errors="strict")
    _require(0 < len(raw) <= MAX_PATCH_BYTES, "Patch is empty or exceeds 1 MB.")
    _require("\x00" not in patch, "NUL/binary content is forbidden.")
    _require(not patch.startswith("GIT binary patch"), "Binary patches are forbidden.")
    lines = patch.splitlines()
    _require(len(lines) <= MAX_LINES, "Patch has too many lines.")
    headers = []
    for line in lines:
        if line.startswith("diff --git "):
            match = re.fullmatch(r"diff --git a/(.+) b/(.+)", line)
            _require(match is not None, "Malformed or unsafe diff header.")
            old, new = match.groups()
            _require(old == new, "Renames are not allowed in unattended repairs.")
            headers.append(new)
        if line.startswith(("new file mode 120000", "old mode 120000",
                            "new file mode 160000", "old mode 160000")):
            raise GateError("Symlinks and submodules are forbidden.")
        if line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            raise GateError("Renames and copies are forbidden.")
        if line.startswith("+++ /dev/null"):
            raise GateError("File deletion is forbidden.")
    _require(0 < len(headers) <= MAX_FILES, "Patch must change 1–40 files.")
    _require(len(headers) == len(set(headers)), "Duplicate file paths in patch.")
    denied = [path for path in headers if not _path_ok(path, allow_patterns)]
    _require(not denied, "Patch touches paths outside the owner allowlist: " +
             ", ".join(denied[:8]))
    # Require normal unified-diff hunks, not an arbitrary file disguised as a patch.
    _require(any(line.startswith("@@ ") for line in lines), "Patch has no unified-diff hunks.")
    return {"schema": SCHEMA, "valid": True, "files": headers,
            "file_count": len(headers), "patch_bytes": len(raw),
            "patch_sha256": __import__("hashlib").sha256(raw).hexdigest(),
            "automatic_merge_eligible": False}


def merge_decision(evidence: dict) -> dict:
    """Return eligibility only; evidence authenticity must be checked by caller.

    Automatic merge is deliberately restricted to low-risk, same-repository
    patches with real branch protection, exact-SHA required checks, an independent
    trusted approval, no conflicts, and a verified disposable test worker.
    """
    _require(isinstance(evidence, dict) and set(evidence) == REQUIRED_EVIDENCE,
             "Merge evidence has missing or unknown fields.")
    _require(evidence["schema"] == SCHEMA, "Unsupported evidence schema.")
    _require(isinstance(evidence["repository"], str) and
             re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", evidence["repository"]),
             "Invalid repository.")
    for name in ("base_sha", "candidate_sha"):
        _require(isinstance(evidence[name], str) and SHA.fullmatch(evidence[name]),
                 "Immutable " + name + " required.")
    _require(type(evidence["pr_number"]) is int and evidence["pr_number"] > 0,
             "A real pull request number is required.")
    _require(evidence["risk"] in ("low", "medium", "high"), "Invalid risk tier.")
    _require(evidence["risk"] == "low", "Only low-risk patches may be auto-merge eligible.")
    _require(evidence["trusted_actor"] is True, "Patch author is not trusted.")
    _require(evidence["source_isolated"] is True,
             "Patch was not tested in a verified disposable isolated worker.")
    _require(evidence["branch_protection"] == {
        "enabled": True, "required_checks_enforced": True,
        "review_enforced": True, "admin_bypass": False,
    }, "Verified branch protection and no-bypass policy are required.")
    _require(evidence["merge_state"] == "clean", "PR is not cleanly mergeable.")
    paths = evidence["patch_paths"]
    _require(isinstance(paths, list) and 0 < len(paths) <= MAX_FILES,
             "Invalid patch path list.")
    _require(all(isinstance(p, str) and _path_ok(p, ["src/**", "lib/**", "tests/**",
                 "*.py", "*.js", "*.ts", "*.tsx", "*.css", "*.md"])
                 for p in paths), "Patch contains a protected or disallowed path.")
    required = evidence["required_checks"]
    checks = evidence["checks"]
    _require(isinstance(required, list) and 1 <= len(required) <= 30 and
             len(set(required)) == len(required), "Invalid required-check list.")
    _require(isinstance(checks, dict) and set(checks) == set(required),
             "Every required check must have matching evidence.")
    for name in required:
        item = checks[name]
        _require(isinstance(item, dict) and set(item) ==
                 {"candidate_sha", "conclusion", "observed", "log_sha256",
                  "issuer_verified"}, "Malformed check evidence: " + str(name))
        _require(item["candidate_sha"] == evidence["candidate_sha"] and
                 item["conclusion"] == "success" and item["observed"] is True and
                 item["issuer_verified"] is True and
                 isinstance(item["log_sha256"], str) and
                 re.fullmatch(r"[a-f0-9]{64}", item["log_sha256"]),
                 "Required check failed or does not prove the exact candidate SHA.")
    review = evidence["review"]
    _require(isinstance(review, dict) and set(review) ==
             {"approved", "reviewer_trusted", "reviewer_is_author",
              "candidate_sha", "pr_number", "repository"},
             "Malformed independent review evidence.")
    _require(review["candidate_sha"] == evidence["candidate_sha"] and
             review["pr_number"] == evidence["pr_number"] and
             review["repository"] == evidence["repository"],
             "Review does not bind the exact repository, PR and candidate SHA.")
    _require(review["approved"] is True and review["reviewer_trusted"] is True and
             review["reviewer_is_author"] is False,
             "A separate trusted reviewer must approve the exact candidate.")
    return {"schema": SCHEMA, "eligible": True, "repository": evidence["repository"],
            "candidate_sha": evidence["candidate_sha"], "pr_number": evidence["pr_number"],
            "decision": "eligible_for_policy-controlled_auto_merge",
            "merge_performed": False,
            "note": "Eligibility only. The caller must authenticate provider evidence and invoke the approved merge executor."}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    patch_cmd = sub.add_parser("validate-patch")
    patch_cmd.add_argument("--patch", type=Path, required=True)
    patch_cmd.add_argument("--allow", action="append", required=True,
                           help="Owner-controlled allowed path glob; repeat as needed.")
    decision_cmd = sub.add_parser("merge-decision")
    decision_cmd.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        path = args.patch if args.command == "validate-patch" else args.evidence
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_PATCH_BYTES:
            raise GateError("Input must be a bounded regular non-symlink file.")
        if args.command == "validate-patch":
            result = validate_patch(path.read_text("utf-8"), allow_patterns=args.allow)
        else:
            result = merge_decision(json.loads(path.read_text("utf-8")))
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError, GateError) as exc:
        print("Repair gate denied: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
