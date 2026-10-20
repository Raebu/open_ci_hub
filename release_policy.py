#!/usr/bin/env python3
"""Fail-closed cross-provider release routing for trusted native PC workers.

This module makes *decisions*; it does not execute untrusted branch code or
confer authentication. Event provenance must be established by GitHub/GitLab
native runners (or a verified ingress) before a job can be admitted.
"""
from __future__ import annotations

import argparse
from fnmatch import fnmatchcase
import json
import os
from pathlib import Path
import re
import sys

SCHEMA = "open-ci-hub.release-policy.v1"
SHA = re.compile(r"^[a-f0-9]{40}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+$")
REF = re.compile(r"^refs/(?:heads|tags)/[A-Za-z0-9][A-Za-z0-9._/-]{0,250}$")
ACTOR = re.compile(r"^[A-Za-z0-9_.@-]{1,120}$")
PATTERN = re.compile(r"^[A-Za-z0-9*?.][A-Za-z0-9*?._/-]{0,200}$")
TARGETS = {"pc", "remote"}
PROVIDERS = {"github", "gitlab"}
EVENTS = {"push", "pull_request", "merge_request"}
MAX_FIXES = 3


def _must(test, why):
    if not test:
        raise ValueError(why)


def validate_config(data):
    _must(isinstance(data, dict) and set(data) == {"schema", "projects"}, "Invalid policy envelope.")
    _must(data["schema"] == SCHEMA, "Unsupported policy version.")
    projects = data["projects"]
    _must(isinstance(projects, list) and len(projects) <= 100, "Projects must be a bounded array.")
    seen = set()
    for p in projects:
        expected = {"provider", "repository", "trusted_actors", "ci_branches",
                    "ci_tags", "production_refs", "preview_refs", "deploy_target",
                    "production_auto", "preview_auto", "pr_isolation_verified",
                    "auto_fix", "max_fix_attempts", "required_checks"}
        _must(isinstance(p, dict) and set(p) == expected, "Project policy fields missing or unrecognised.")
        _must(p["provider"] in PROVIDERS and isinstance(p["repository"], str)
              and REPOSITORY.fullmatch(p["repository"]), "Invalid provider/repository.")
        key = (p["provider"], p["repository"].casefold())
        _must(key not in seen, "Duplicate project policy.")
        seen.add(key)
        _must(isinstance(p["trusted_actors"], list) and 0 < len(p["trusted_actors"]) <= 25
              and all(isinstance(v, str) and ACTOR.fullmatch(v) for v in p["trusted_actors"]),
              "Invalid trusted actors.")
        for name in ("ci_branches", "ci_tags", "production_refs", "preview_refs"):
            _must(isinstance(p[name], list) and len(p[name]) <= 30
                  and all(isinstance(v, str) and PATTERN.fullmatch(v) for v in p[name]),
                  "Invalid ref pattern list: " + name)
        _must(p["deploy_target"] in TARGETS, "Choose pc or remote deployment.")
        for boolean in ("production_auto", "preview_auto", "pr_isolation_verified", "auto_fix"):
            _must(type(p[boolean]) is bool, boolean + " must be boolean.")
        _must(type(p["max_fix_attempts"]) is int and
              0 <= p["max_fix_attempts"] <= MAX_FIXES, "Invalid fix-attempt limit.")
        _must(isinstance(p["required_checks"], list) and
              1 <= len(p["required_checks"]) <= 30 and
              all(isinstance(v, str) and re.fullmatch(r"[a-z][a-z0-9_.-]{0,50}", v)
                  for v in p["required_checks"]) and
              len(set(p["required_checks"])) == len(p["required_checks"]),
              "At least one distinct explicit CI check is required.")
        _must(not p["production_auto"] or p["production_refs"],
              "Autonomous production needs release ref allowlists.")
        _must(not p["preview_auto"] or p["preview_refs"],
              "Autonomous preview needs branch allowlists.")
        _must(not p["auto_fix"] or p["max_fix_attempts"] > 0,
              "Autofix must have a finite positive budget.")
    return data


def validate_event(event):
    expected = {"provider", "repository", "kind", "ref", "sha", "actor",
                "from_fork", "source_repo", "delivery_id"}
    _must(isinstance(event, dict) and set(event) == expected, "Invalid normalised event.")
    _must(event["provider"] in PROVIDERS and isinstance(event["repository"], str)
          and REPOSITORY.fullmatch(event["repository"]), "Unknown repository.")
    _must(event["kind"] in EVENTS, "Unsupported event kind.")
    _must(isinstance(event["ref"], str) and REF.fullmatch(event["ref"]),
          "Invalid full Git ref.")
    _must(isinstance(event["sha"], str) and SHA.fullmatch(event["sha"]),
          "Immutable commit SHA required.")
    _must(isinstance(event["actor"], str) and ACTOR.fullmatch(event["actor"]),
          "Invalid authenticated actor.")
    _must(type(event["from_fork"]) is bool, "Invalid fork status.")
    _must(isinstance(event["source_repo"], str)
          and REPOSITORY.fullmatch(event["source_repo"]), "Invalid source repo.")
    _must(isinstance(event["delivery_id"], str) and
          re.fullmatch(r"[A-Za-z0-9_-]{8,120}", event["delivery_id"]),
          "Idempotent delivery ID required.")
    return event


def _matches(ref, patterns):
    return any(fnmatchcase(ref, pattern) for pattern in patterns)


def plan(policy, event):
    """Plan jobs without executing or accepting a caller's success assertion."""
    validate_config(policy)
    validate_event(event)
    p = next((project for project in policy["projects"]
              if project["provider"] == event["provider"] and
              project["repository"].casefold() == event["repository"].casefold()), None)
    base = {"schema": "open-ci-hub.release-plan.v1",
            "provider": event["provider"], "repository": event["repository"],
            "ref": event["ref"], "sha": event["sha"],
            "delivery_id": event["delivery_id"], "run_ci": False,
            "deployment": "none", "status": "blocked", "reason": "",
            "required_checks": [], "autofix_budget": 0}
    if p is None:
        return {**base, "reason": "Repository not explicitly enrolled."}
    if (event["actor"].casefold() not in {x.casefold() for x in p["trusted_actors"]}):
        return {**base, "reason": "Actor is not a trusted repository contributor."}
    if event["from_fork"] or (event["source_repo"].casefold() != event["repository"].casefold()):
        return {**base, "reason": "Fork code requires a disposable isolated runner, not the persistent PC."}
    if event["kind"] in ("pull_request", "merge_request") and not p["pr_isolation_verified"]:
        return {**base, "reason": "PR/MR requires verified disposable isolation before PC execution."}
    kind, relative = event["ref"].split("/", 2)[1:]
    if kind == "heads" and not _matches(relative, p["ci_branches"]):
        return {**base, "reason": "Branch is outside CI scope."}
    if kind == "tags" and not _matches(relative, p["ci_tags"]):
        return {**base, "reason": "Tag is outside CI scope."}
    if event["kind"] in ("pull_request", "merge_request") and kind != "heads":
        return {**base, "reason": "PR/MR source must be a branch."}
    result = {**base, "status": "admitted", "reason": "Native CI permitted",
              "run_ci": True, "required_checks": p["required_checks"],
              "autofix_budget": p["max_fix_attempts"] if p["auto_fix"] else 0}
    # No deployment from PR/MR events, even from an authenticated collaborator.
    if event["kind"] != "push":
        return result
    if p["production_auto"] and _matches(event["ref"], p["production_refs"]):
        result["deployment"] = "production-" + p["deploy_target"]
    elif p["preview_auto"] and _matches(event["ref"], p["preview_refs"]):
        result["deployment"] = "preview-" + p["deploy_target"]
    return result


def ready_to_deploy(plan_data, receipt):
    """Fail closed on missing, superseded, mismatched, cancelled or failed CI.

    A downstream executor MUST independently authenticate the receipt issuer,
    verify logs and confirm the immutable checked-out SHA; this pure function
    is only one gate, not a signature verifier.
    """
    if (not isinstance(plan_data, dict) or plan_data.get("status") != "admitted"
        or not plan_data.get("run_ci") or plan_data.get("deployment") == "none"):
        return False
    if not isinstance(receipt, dict) or set(receipt) != {
        "repository", "sha", "delivery_id", "status", "checks", "checked_out_sha",
        "verified_issuer", "run_id"}:
        return False
    if (receipt["repository"] != plan_data["repository"] or
        receipt["sha"] != plan_data["sha"] or
        receipt["checked_out_sha"] != plan_data["sha"] or
        receipt["delivery_id"] != plan_data["delivery_id"] or
        receipt["status"] != "passed" or
        receipt["verified_issuer"] is not True or
        not isinstance(receipt["run_id"], str) or not receipt["run_id"]):
        return False
    checks = receipt["checks"]
    if not isinstance(checks, dict) or set(checks) != set(plan_data["required_checks"]):
        return False
    return all(isinstance(checks[step], dict) and set(checks[step]) ==
               {"exit_code", "log_sha256", "observed", "timed_out"}
               and type(checks[step]["exit_code"]) is int
               and checks[step]["exit_code"] == 0
               and checks[step]["timed_out"] is False
               and checks[step]["observed"] is True
               and isinstance(checks[step]["log_sha256"], str)
               and re.fullmatch(r"[a-f0-9]{64}", checks[step]["log_sha256"])
               for step in plan_data["required_checks"])


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--policy", type=Path, required=True,
                     help="Locally managed allowlist; not taken from untrusted repository code")
    cli.add_argument("--event", type=Path, required=True,
                     help="Event envelope from verified provider ingress")
    args = cli.parse_args(argv)
    try:
        if args.policy.is_symlink() or args.event.is_symlink():
            raise PermissionError("Refusing symlinked policy/event files.")
        policy = json.loads(args.policy.read_text("utf-8"))
        event = json.loads(args.event.read_text("utf-8"))
        print(json.dumps(plan(policy, event), sort_keys=True, indent=2))
        return 0
    except (OSError, ValueError, TypeError, PermissionError) as exc:
        print("Release routing denied: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
