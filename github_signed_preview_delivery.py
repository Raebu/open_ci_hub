#!/usr/bin/env python3
"""Private-GitHub signed-manifest delivery to owner-enrolled PC preview queue.

No public listener. A GitHub issue is only a transport: Ed25519 verification,
exact commit and artifact hash remain mandatory. This module cannot sign tasks,
enable consent, start production deployment or execute runner-provided scripts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

import provider_artifact_verifier as provider
import release_status_consistency as consistency
import signed_preview_queue as preview
import pc_releases as releases
from hub import secure_write

CONFIG_NAME = "github-delivery.json"
SCHEMA = "open-ci-hub.github-delivery.v1"
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
LOGIN = re.compile(r"^[A-Za-z0-9-]{1,39}$")
MAX_TASKS = 10
MAX_BODY = 16384


def _config_path(app):
    return releases._app_dir(app) / CONFIG_NAME


def config(app):
    data = releases._private_file(_config_path(app))
    if not isinstance(data, dict) or set(data) != {
        "schema", "control_repo", "control_repo_id", "trusted_issue_authors",
        "artifact_name", "enrolled_at", "status_context", "status_publisher"
    } or data["schema"] != SCHEMA:
        raise PermissionError("Private GitHub delivery not enrolled.")
    if (not isinstance(data["control_repo"], str) or not REPO.fullmatch(data["control_repo"])
        or type(data["control_repo_id"]) is not int or data["control_repo_id"] < 1
        or not isinstance(data["trusted_issue_authors"], list)
        or not 1 <= len(data["trusted_issue_authors"]) <= 10
        or any(not isinstance(x, str) or not LOGIN.fullmatch(x)
               for x in data["trusted_issue_authors"])
        or not isinstance(data["artifact_name"], str)
        or not provider.NAME.fullmatch(data["artifact_name"])
        or type(data["enrolled_at"]) is not int or data["enrolled_at"] < 1
        or not isinstance(data["status_context"], str)
        or not 1 <= len(data["status_context"]) <= 90
        or not isinstance(data["status_publisher"], str)
        or not re.fullmatch(r"[A-Za-z0-9_.\[\]-]{1,90}", data["status_publisher"])):
        raise PermissionError("Invalid owner-enrolled transport scope.")
    return data


def enrol(app, *, control_repo, trusted_issue_authors, artifact_name,
          status_context="Raeburn Local CI",
          status_publisher="raeburn-local-ci[bot]", confirm=False,
          api=provider._json):
    releases._owner()
    releases._config(app)
    if not confirm:
        raise PermissionError("Owner must approve exact private GitHub delivery source.")
    if (not isinstance(control_repo, str) or not REPO.fullmatch(control_repo)
        or not isinstance(trusted_issue_authors, list) or not trusted_issue_authors
        or any(not isinstance(v, str) or not LOGIN.fullmatch(v)
               for v in trusted_issue_authors)
        or not isinstance(artifact_name, str) or not provider.NAME.fullmatch(artifact_name)
        or not isinstance(status_context, str) or not 1 <= len(status_context) <= 90
        or not isinstance(status_publisher, str)
        or not re.fullmatch(r"[A-Za-z0-9_.\[\]-]{1,90}", status_publisher)):
        raise ValueError("Invalid private GitHub transport configuration.")
    path = _config_path(app)
    if path.exists() or path.is_symlink():
        raise PermissionError("Already enrolled; explicit audited rotation required.")
    info = api("gh", "repos/" + control_repo)
    if (not isinstance(info, dict) or info.get("private") is not True
        or not isinstance(info.get("id"), int)
        or info.get("full_name", "").casefold() != control_repo.casefold()):
        raise PermissionError("Transport repository must exist and be private.")
    record = {"schema": SCHEMA, "control_repo": control_repo,
              "control_repo_id": info["id"], "trusted_issue_authors": trusted_issue_authors,
              "artifact_name": artifact_name, "enrolled_at": int(time.time()),
              "status_context": status_context, "status_publisher": status_publisher}
    secure_write(path, json.dumps(record, sort_keys=True) + "\n")
    return {"app": app, "transport_enrolled": True, "repository_id": info["id"],
            "polling_enabled": False}


def _verify_private_repository(conf, api):
    info = api("gh", "repos/" + conf["control_repo"])
    if (not isinstance(info, dict) or info.get("private") is not True
        or info.get("id") != conf["control_repo_id"]
        or str(info.get("full_name", "")).casefold() !=
           conf["control_repo"].casefold()):
        raise PermissionError("Private transport repo ID/privacy changed.")


def _fetch_signed_zip(app, signed, artifact_name, *, api, binary,
                      status_context, status_publisher):
    proof = preview.verify_signed(app, signed)
    repo = proof["repository"]
    if not REPO.fullmatch(repo):
        raise PermissionError("Signed release repo unsupported by GitHub-only delivery.")
    rid = proof["run_id"]
    if not provider.RUN.fullmatch(rid):
        raise PermissionError("This transport accepts numeric GitHub run IDs only.")
    base = "repos/" + repo + "/actions"
    run = api("gh", base + "/runs/" + rid)
    if (not isinstance(run, dict) or run.get("event") != "push"
        or run.get("status") != "completed" or run.get("conclusion") != "success"
        or run.get("head_sha") != proof["commit"]
        or run.get("head_branch") != proof["ref"][len("refs/heads/"):]
        or (run.get("head_repository") or {}).get("full_name", "").casefold()
           != repo.casefold()):
        raise PermissionError("Signed preview cannot be matched to a successful GitHub push.")
    # Native success cannot override a failed, superseded or prematurely green
    # independent required CI status for the same SHA.
    consistency.verify_github_release_statuses(
        api=api, repository=repo, sha=proof["commit"], run=run,
        required_context=status_context, trusted_publisher=status_publisher)
    arts = api("gh", base + "/runs/" + rid + "/artifacts?per_page=100")
    if (not isinstance(arts, dict) or not isinstance(arts.get("artifacts"), list)
        or arts.get("total_count", 0) > 100):
        raise PermissionError("Incomplete GitHub artifact listing.")
    matches = [a for a in arts["artifacts"]
               if a.get("name") == artifact_name and a.get("expired") is False]
    if len(matches) != 1 or type(matches[0].get("id")) is not int:
        raise PermissionError("Expected exactly one live GitHub release artifact.")
    a = matches[0]
    digest = a.get("digest")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise PermissionError("No authenticated artifact ZIP digest.")
    wrapper = binary("gh", base + "/artifacts/" + str(a["id"]) + "/zip",
                     size_limit=provider.MAX_BUNDLE)
    if hashlib.sha256(wrapper).hexdigest() != digest[7:]:
        raise PermissionError("Provider artifact wrapper digest mismatch.")
    release = provider._bundle(wrapper)
    if hashlib.sha256(release).hexdigest() != proof["artifact_sha256"]:
        raise PermissionError("Downloaded release differs from the signed exact artifact.")
    return release


def poll_once(app, *, api=provider._json, binary=provider._query):
    releases._owner()
    owner_policy = preview._policy(app)
    if not owner_policy["preview_auto"]:
        raise PermissionError("No standing local preview consent.")
    conf = config(app)
    _verify_private_repository(conf, api)
    issues = api("gh", "repos/" + conf["control_repo"] + "/issues?state=open&per_page=100")
    if not isinstance(issues, list) or len(issues) > 100:
        raise PermissionError("Invalid control issue listing.")
    accepted = []
    # Do not interpret issue authorship as evidence a user issued a command:
    # a valid issuer signature and exact downloaded source bytes are compulsory.
    for issue in issues:
        if len(accepted) >= MAX_TASKS:
            break
        if (not isinstance(issue, dict) or issue.get("pull_request") is not None
            or issue.get("state") != "open"
            or not str(issue.get("title", "")).startswith("[open-ci-preview] ")
            or (issue.get("user") or {}).get("login", "").casefold() not in
               {x.casefold() for x in conf["trusted_issue_authors"]}
            or not isinstance(issue.get("body"), str)
            or len(issue["body"].encode()) > MAX_BODY):
            continue
        try:
            signed = json.loads(issue["body"])
            proof = preview.verify_signed(app, signed)
            if proof["issued_at"] <= conf["enrolled_at"] - 60:
                raise PermissionError("Refusing pre-enrolment signed delivery.")
            release = _fetch_signed_zip(app, signed, conf["artifact_name"],
                                        api=api, binary=binary,
                                        status_context=conf["status_context"],
                                        status_publisher=conf["status_publisher"])
            folder, _, _ = preview._dirs(app)
            with tempfile.TemporaryDirectory(prefix=".delivery-", dir=folder) as temp:
                md = Path(temp) / "manifest.json"
                zip_path = Path(temp) / "release.zip"
                md.write_text(json.dumps(signed), encoding="utf-8")
                zip_path.write_bytes(release)
                response = preview.submit(app, md, zip_path)
            accepted.append({"issue": issue.get("number"), "status": response["status"],
                             "run_id": response["run_id"], "commit": response["commit"]})
        except (OSError, ValueError, PermissionError, TypeError):
            # Fail closed, keep the private issue for operator investigation.
            continue
    return {"app": app, "queued": accepted, "count": len(accepted),
            "message": "Only signed, exact-SHA, exact-hash previews queued; nothing deployed."}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    e = sub.add_parser("enrol")
    e.add_argument("app")
    e.add_argument("--repo", required=True)
    e.add_argument("--trusted-author", action="append", required=True)
    e.add_argument("--artifact-name", default="release-bundle")
    e.add_argument("--status-context", default="Raeburn Local CI")
    e.add_argument("--status-publisher", default="raeburn-local-ci[bot]")
    e.add_argument("--confirm", action="store_true")
    for action in ("status", "poll-once"):
        x = sub.add_parser(action)
        x.add_argument("app")
    a = p.parse_args(argv)
    try:
        if a.action == "enrol":
            data = enrol(a.app, control_repo=a.repo,
                         trusted_issue_authors=a.trusted_author,
                         artifact_name=a.artifact_name,
                         status_context=a.status_context,
                         status_publisher=a.status_publisher,
                         confirm=a.confirm)
        elif a.action == "status":
            data = config(a.app)
            data = {"app": a.app, "repo": data["control_repo"],
                    "artifact_name": data["artifact_name"],
                    "queued": preview.status(a.app)["queue"]}
        else:
            data = poll_once(a.app)
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, PermissionError, TypeError) as e:
        import sys
        print("GitHub signed delivery denied: " + str(e)[:240], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
