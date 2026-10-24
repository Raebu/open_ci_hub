#!/usr/bin/env python3
"""Read-only private GitLab signed preview transport to the local PC queue.

A GitLab issue is a nonauthoritative mailbox: exact Ed25519 signature, source
pipeline/jobs/log hashes, runner identity and downloaded artifact hash are
checked independently against authenticated GitLab API before enqueueing.

No deployment occurs in this module. Requires owner-enrolled consent; never
writes to GitLab, invokes untrusted shell or accesses signing private keys.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import quote

import pc_releases as releases
import provider_artifact_verifier as provider
import signed_preview_queue as preview
from hub import secure_write

SCHEMA = "open-ci-hub.gitlab-delivery.v1"
SEGMENT = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
USERNAME = re.compile(r"^[A-Za-z0-9_.@-]{1,100}$")
MAX_MESSAGES = 10
MAX_BODY = 16384


def _valid_project(p):
    return (isinstance(p, str) and len(p) <= 250 and
            2 <= len(p.split("/")) <= 8 and
            all(SEGMENT.fullmatch(part) and part not in (".", "..")
                for part in p.split("/")))


def _config_path(app):
    return releases._app_dir(app) / "gitlab-delivery.json"


def config(app):
    data = releases._private_file(_config_path(app))
    if (not isinstance(data, dict) or set(data) != {
        "schema", "control_project", "control_id", "trusted_authors",
        "source_actor", "artifact_job", "enrolled_at"
    } or data["schema"] != SCHEMA or not _valid_project(data["control_project"])
        or type(data["control_id"]) is not int or data["control_id"] < 1
        or not isinstance(data["trusted_authors"], list)
        or not 1 <= len(data["trusted_authors"]) <= 10
        or not all(isinstance(x, str) and USERNAME.fullmatch(x)
                   for x in data["trusted_authors"])
        or not isinstance(data["source_actor"], str)
        or not USERNAME.fullmatch(data["source_actor"])
        or not isinstance(data["artifact_job"], str)
        or not provider.NAME.fullmatch(data["artifact_job"])
        or type(data["enrolled_at"]) is not int or data["enrolled_at"] < 1):
        raise PermissionError("Invalid locally pinned GitLab delivery policy.")
    return data


def enrol(app, *, control_project, trusted_authors, source_actor,
          artifact_job="build", confirm=False, api=provider._json):
    releases._owner()
    releases._config(app)
    if not confirm:
        raise PermissionError("Owner must authorise exact GitLab control project.")
    if (not _valid_project(control_project)
        or not isinstance(trusted_authors, list)
        or not 1 <= len(trusted_authors) <= 10
        or not all(isinstance(x, str) and USERNAME.fullmatch(x)
                   for x in trusted_authors)
        or not isinstance(source_actor, str)
        or not USERNAME.fullmatch(source_actor)
        or not isinstance(artifact_job, str)
        or not provider.NAME.fullmatch(artifact_job)):
        raise ValueError("Invalid GitLab transport enrolment.")
    target = _config_path(app)
    if target.exists() or target.is_symlink():
        raise PermissionError("GitLab project already enrolled; rotate with review.")
    info = api("glab", "projects/" + quote(control_project, safe=""))
    if (not isinstance(info, dict) or info.get("visibility") != "private"
        or type(info.get("id")) is not int
        or str(info.get("path_with_namespace", "")).casefold() != control_project.casefold()):
        raise PermissionError("Control project must be verified private GitLab project.")
    data = {"schema": SCHEMA, "control_project": control_project,
            "control_id": info["id"], "trusted_authors": trusted_authors,
            "source_actor": source_actor, "artifact_job": artifact_job,
            "enrolled_at": int(time.time())}
    secure_write(target, json.dumps(data, sort_keys=True) + "\n")
    return {"app": app, "gitlab_transport_enrolled": True,
            "verified_project_id": info["id"], "polling_enabled": False}


def _verify_control(data, api):
    info = api("glab", "projects/" + str(data["control_id"]))
    if (not isinstance(info, dict) or info.get("visibility") != "private"
        or info.get("id") != data["control_id"]
        or str(info.get("path_with_namespace", "")).casefold() !=
           data["control_project"].casefold()):
        raise PermissionError("GitLab control project privacy/identity mismatch.")


def _verify_release(app, signed, policy, conf, api, binary):
    proof = preview.verify_signed(app, signed)
    if not _valid_project(proof["repository"]):
        raise PermissionError("Invalid GitLab source project.")
    if not provider.RUN.fullmatch(proof["run_id"]):
        raise PermissionError("GitLab pipeline must have a numeric ID.")
    record, release = provider.verify_gitlab(
        proof["repository"], proof["run_id"], proof["commit"], proof["ref"],
        conf["source_actor"], list(policy["required_checks"]),
        artifact_job=conf["artifact_job"], api=api, binary=binary)
    if (record["repository"] != proof["repository"]
        or record["ref"] != proof["ref"]
        or record["run_id"] != proof["run_id"]
        or record["commit"] != proof["commit"]
        or record["checked_out_sha"] != proof["checked_out_sha"]
        or record["run_status"] != "passed"
        or record["checks"] != proof["checks"]
        or record["artifact_sha256"] != proof["artifact_sha256"]
        or hashlib.sha256(release).hexdigest() != proof["artifact_sha256"]):
        raise PermissionError("Signed GitLab checks/logs/artifact do not match provider evidence.")
    return release


def poll_once(app, *, api=provider._json, binary=provider._query):
    releases._owner()
    policy = preview._policy(app)
    if not policy["preview_auto"]:
        raise PermissionError("Standing preview consent is disabled.")
    conf = config(app)
    _verify_control(conf, api)
    issues = api("glab", "projects/" + str(conf["control_id"]) +
                 "/issues?state=opened&per_page=100")
    if not isinstance(issues, list) or len(issues) > 100:
        raise PermissionError("Unbounded GitLab issue result.")
    accepted = []
    for issue in issues:
        if len(accepted) >= MAX_MESSAGES:
            break
        if (not isinstance(issue, dict) or issue.get("state") != "opened"
            or not isinstance(issue.get("title"), str)
            or not issue["title"].startswith("[open-ci-preview] ")
            or (issue.get("author") or {}).get("username", "").casefold() not in
              {x.casefold() for x in conf["trusted_authors"]}
            or not isinstance(issue.get("description"), str)
            or len(issue["description"].encode("utf-8")) > MAX_BODY):
            continue
        try:
            signed = json.loads(issue["description"])
            proof = preview.verify_signed(app, signed)
            if proof["issued_at"] <= conf["enrolled_at"] - 60:
                raise PermissionError("Pre-enrolment signed request is invalid.")
            release = _verify_release(app, signed, policy, conf, api, binary)
            folder, _, _ = preview._dirs(app)
            with tempfile.TemporaryDirectory(prefix=".gitlab-delivery-", dir=folder) as staging:
                manifest = Path(staging) / "signed.json"
                archive = Path(staging) / "release.zip"
                manifest.write_text(json.dumps(signed), encoding="utf-8")
                archive.write_bytes(release)
                queued = preview.submit(app, manifest, archive)
            accepted.append({"issue_iid": issue.get("iid"),
                             "status": queued["status"], "run_id": queued["run_id"],
                             "commit": queued["commit"]})
        except (OSError, PermissionError, ValueError, TypeError):
            continue
    return {"app": app, "queued": accepted, "count": len(accepted),
            "message": "Signed, provider-matched GitLab previews queued only."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    e = sub.add_parser("enrol")
    e.add_argument("app")
    e.add_argument("--control-project", required=True)
    e.add_argument("--trusted-author", action="append", required=True)
    e.add_argument("--source-actor", required=True)
    e.add_argument("--artifact-job", default="build")
    e.add_argument("--confirm", action="store_true")
    for action in ("status", "poll-once"):
        p = sub.add_parser(action)
        p.add_argument("app")
    args = parser.parse_args(argv)
    try:
        if args.action == "enrol":
            result = enrol(args.app, control_project=args.control_project,
                           trusted_authors=args.trusted_author,
                           source_actor=args.source_actor,
                           artifact_job=args.artifact_job,
                           confirm=args.confirm)
        elif args.action == "status":
            data = config(args.app)
            result = {"app": args.app, "control_project": data["control_project"],
                      "source_actor": data["source_actor"],
                      "queue": preview.status(args.app)["queue"]}
        else:
            result = poll_once(args.app)
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    except (OSError, PermissionError, ValueError, TypeError) as error:
        import sys
        print("GitLab delivery refused: " + str(error)[:230], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
