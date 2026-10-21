#!/usr/bin/env python3
"""Read-only GitHub/GitLab CI evidence and authenticated artifact downloader.

Emits UN-SIGNED candidate receipts. Never signs or authorises a deployment.
Use only with a separate attestor that independently checks this evidence;
provider "success" alone is not proof of trusted execution.
"""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from urllib.parse import quote
from zipfile import ZipFile, BadZipFile

REPO = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+$")
SHA = re.compile(r"^[a-f0-9]{40}$")
NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
RUN = re.compile(r"^[1-9][0-9]{0,17}$")
MAX_JSON = 2_000_000
MAX_LOG = 4_000_000
MAX_BUNDLE = 128 * 1024 * 1024
MAX_ARCHIVE = 128 * 1024 * 1024
MAX_CHECKS = 20


def _require(test, reason):
    if not test:
        raise PermissionError(reason)


def _query(tool, endpoint, *, size_limit=MAX_JSON):
    """Query provider API using its separately authenticated official CLI."""
    _require(tool in ("gh", "glab"), "Unsupported provider CLI.")
    _require(isinstance(endpoint, str) and endpoint.startswith(("repos/", "projects/"))
             and "\n" not in endpoint and "\x00" not in endpoint,
             "Unsafe provider API path.")
    command = [tool, "api", endpoint]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    chunks = []
    total = 0
    try:
        while True:
            block = process.stdout.read(1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > size_limit:
                raise PermissionError("Provider API response exceeds size limit.")
            chunks.append(block)
        try:
            status = process.wait(timeout=35)
        except subprocess.TimeoutExpired:
            raise PermissionError("Provider API command timed out.") from None
        _require(status == 0, "Provider API authentication or response failed.")
        return b"".join(chunks)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if process.stdout is not None:
            process.stdout.close()


def _json(tool, endpoint):
    try:
        result = json.loads(_query(tool, endpoint))
    except (ValueError, UnicodeError):
        raise PermissionError("Invalid authenticated provider API JSON.") from None
    return result


def _bundle(data: bytes):
    """Artifact wrapper contains exactly release.zip, with no attacker paths."""
    _require(isinstance(data, bytes) and 0 < len(data) <= MAX_BUNDLE,
             "Invalid artifact bundle size.")
    try:
        with ZipFile(BytesIO(data)) as outer:
            members = outer.infolist()
            _require(len(members) == 1 and members[0].filename == "release.zip"
                     and not members[0].is_dir(), "Unexpected artifact bundle files.")
            file = members[0]
            mode = (file.external_attr >> 16) & 0o170000
            _require(mode in (0, stat.S_IFREG), "Untrusted ZIP member kind.")
            _require(0 < file.file_size <= MAX_ARCHIVE, "Unbounded release artifact.")
            inner = outer.read(file)
            _require(len(inner) == file.file_size, "Incomplete release ZIP.")
            with ZipFile(BytesIO(inner)) as check:
                _require(any(not m.is_dir() for m in check.infolist()),
                         "Empty application release archive.")
    except (OSError, ValueError, BadZipFile, RuntimeError, OverflowError):
        raise PermissionError("Artifact ZIP structure invalid.") from None
    return inner


def _inputs(repository, run_id, expected_sha, ref, actor, required_checks):
    _require(isinstance(repository, str) and REPO.fullmatch(repository),
             "Exact repository namespace/name required.")
    _require(isinstance(run_id, str) and RUN.fullmatch(run_id),
             "Exact provider run ID required.")
    _require(isinstance(expected_sha, str) and SHA.fullmatch(expected_sha),
             "Immutable source commit SHA required.")
    _require(isinstance(ref, str) and ref.startswith("refs/heads/")
             and len(ref) < 300 and ".." not in ref and "//" not in ref
             and "\\" not in ref and " " not in ref,
             "Only a safe exact push branch ref is accepted.")
    _require(isinstance(actor, str) and NAME.fullmatch(actor),
             "Known authenticated push actor required.")
    _require(isinstance(required_checks, list)
             and 1 <= len(required_checks) <= MAX_CHECKS
             and len(set(required_checks)) == len(required_checks)
             and all(isinstance(x, str) and NAME.fullmatch(x)
                     for x in required_checks), "Explicit unique required check names needed.")


def _record(provider, repository, run_id, expected_sha, ref, actor, checks, release_zip):
    return {"schema": "open-ci-hub.provider-evidence.v1",
            "source": "read-only-provider-API", "independently_attested": False,
            "provider": provider, "repository": repository, "run_id": run_id,
            "commit": expected_sha, "checked_out_sha": expected_sha, "ref": ref,
            "actor": actor, "run_status": "passed",
            "checks": checks, "artifact_sha256": hashlib.sha256(release_zip).hexdigest()}


def verify_github(repository, run_id, expected_sha, ref, actor,
                  required_checks, *, workflow_id, artifact_name="release-bundle",
                  api=_json, binary=_query):
    _inputs(repository, run_id, expected_sha, ref, actor, required_checks)
    _require(type(workflow_id) is int and workflow_id > 0, "Pinned trusted workflow ID required.")
    _require(isinstance(artifact_name, str) and NAME.fullmatch(artifact_name),
             "Invalid trusted artifact name.")
    base = "repos/" + repository + "/actions"
    run = api("gh", base + "/runs/" + run_id)
    _require(isinstance(run, dict) and run.get("event") == "push"
             and run.get("status") == "completed" and run.get("conclusion") == "success"
             and run.get("head_sha") == expected_sha
             and run.get("head_branch") == ref[len("refs/heads/"):]
             and run.get("workflow_id") == workflow_id
             and (run.get("head_repository") or {}).get("full_name", "").casefold()
                == repository.casefold()
             and (run.get("actor") or {}).get("login", "").casefold()
                == actor.casefold(),
             "GitHub run failed identity, workflow or exact-SHA verification.")
    jobs = api("gh", base + "/runs/" + run_id + "/jobs?per_page=100")
    _require(isinstance(jobs, dict) and isinstance(jobs.get("jobs"), list)
             and jobs.get("total_count", 0) <= 100,
             "Incomplete GitHub job listing.")
    evidence = {}
    used_jobs = set()
    for step_name in required_checks:
        matches = [j for j in jobs["jobs"] if j.get("name") == step_name]
        _require(len(matches) == 1, "Missing or duplicate mandatory GitHub job: " + step_name)
        job = matches[0]
        _require(job.get("status") == "completed" and job.get("conclusion") == "success"
                 and isinstance(job.get("id"), int) and job["id"] > 0,
                 "Required GitHub job incomplete or failed.")
        labels = set(job.get("labels") or [])
        _require({"self-hosted", "linux", "local-ci"}.issubset(
                     {str(x).lower() for x in labels}),
                 "Job did not run on an eligible local Linux runner.")
        _require(isinstance(job.get("runner_name"), str)
                 and job["runner_name"].startswith("open-ci-hub-"),
                 "Local runner identity unavailable.")
        _require(job["id"] not in used_jobs, "CI job reused for multiple required checks.")
        used_jobs.add(job["id"])
        steps = job.get("steps")
        _require(isinstance(steps, list) and steps
                 and all(s.get("status") == "completed"
                         and s.get("conclusion") in ("success", "skipped")
                         for s in steps)
                 and any(s.get("conclusion") == "success" for s in steps),
                 "Job contains incomplete or failed check steps.")
        logs = binary("gh", base + "/jobs/" + str(job["id"]) + "/logs", size_limit=MAX_LOG)
        _require(isinstance(logs, bytes) and bool(logs),
                 "Provider job log is missing.")
        evidence[step_name] = {"observed": True, "exit_code": 0,
                               "timed_out": False,
                               "log_sha256": hashlib.sha256(logs).hexdigest()}
    artifacts = api("gh", base + "/runs/" + run_id + "/artifacts?per_page=100")
    _require(isinstance(artifacts, dict) and isinstance(artifacts.get("artifacts"), list)
             and artifacts.get("total_count", 0) <= 100,
             "Incomplete GitHub artifact inventory.")
    matches = [a for a in artifacts["artifacts"]
               if a.get("name") == artifact_name and a.get("expired") is False]
    _require(len(matches) == 1 and isinstance(matches[0].get("id"), int)
             and matches[0]["id"] > 0, "No unique live build artifact.")
    artifact = matches[0]
    raw = binary("gh", base + "/artifacts/" + str(artifact["id"]) + "/zip",
                 size_limit=MAX_BUNDLE)
    digest = artifact.get("digest")
    if isinstance(digest, str) and digest.startswith("sha256:"):
        _require(hashlib.sha256(raw).hexdigest() == digest[7:],
                 "Downloaded wrapper does not match GitHub artifact digest.")
    inner = _bundle(raw)
    return _record("github", repository, run_id, expected_sha, ref, actor, evidence, inner), inner


def verify_gitlab(repository, run_id, expected_sha, ref, actor,
                  required_checks, *, artifact_job, api=_json, binary=_query):
    _inputs(repository, run_id, expected_sha, ref, actor, required_checks)
    _require(isinstance(artifact_job, str) and NAME.fullmatch(artifact_job),
             "Pinned GitLab artifact job required.")
    project = "projects/" + quote(repository, safe="")
    metadata = api("glab", project)
    _require(isinstance(metadata, dict) and
             metadata.get("path_with_namespace", "").casefold() == repository.casefold()
             and isinstance(metadata.get("id"), int), "GitLab repository mismatch.")
    base = "projects/" + str(metadata["id"])
    pipeline = api("glab", base + "/pipelines/" + run_id)
    _require(isinstance(pipeline, dict) and str(pipeline.get("id")) == run_id
             and pipeline.get("sha") == expected_sha
             and pipeline.get("ref") == ref[len("refs/heads/"):]
             and pipeline.get("status") == "success"
             and pipeline.get("source") == "push"
             and (pipeline.get("user") or {}).get("username", "").casefold()
                 == actor.casefold(),
             "GitLab pipeline failed trusted actor, status, ref or exact-SHA verification.")
    jobs = api("glab", base + "/pipelines/" + run_id + "/jobs?per_page=100&include_retried=true")
    _require(isinstance(jobs, list) and len(jobs) <= 100, "Incomplete GitLab job listing.")
    evidence = {}
    for check in required_checks:
        matches = [j for j in jobs if j.get("name") == check]
        _require(len(matches) == 1, "Missing or ambiguous mandatory GitLab job: " + check)
        job = matches[0]
        _require(job.get("status") == "success" and isinstance(job.get("id"), int)
                 and isinstance(job.get("runner"), dict)
                 and {"local-ci", "local-ci-isolated"}.intersection(
                     {str(x).lower() for x in job.get("tag_list", [])}),
                 "GitLab job not successful on approved local runner.")
        trace = binary("glab", base + "/jobs/" + str(job["id"]) + "/trace",
                       size_limit=MAX_LOG)
        _require(isinstance(trace, bytes) and bool(trace), "Missing provider-trusted GitLab trace.")
        evidence[check] = {"observed": True, "exit_code": 0,
                           "timed_out": False,
                           "log_sha256": hashlib.sha256(trace).hexdigest()}
    artifact_matches = [j for j in jobs if j.get("name") == artifact_job
                        and j.get("status") == "success"]
    _require(len(artifact_matches) == 1 and
             isinstance(artifact_matches[0].get("id"), int),
             "No unique successful GitLab artifact job.")
    blob = binary("glab", base + "/jobs/" + str(artifact_matches[0]["id"]) + "/artifacts",
                  size_limit=MAX_BUNDLE)
    inner = _bundle(blob)
    return _record("gitlab", repository, run_id, expected_sha, ref, actor, evidence, inner), inner


def write_unsigned_evidence(directory, record, content):
    """Write private *unsigned* candidate files; this does not grant consent."""
    folder = Path(directory)
    _require(folder.is_absolute() and not folder.is_symlink() and folder.is_dir(),
             "Existing absolute private output directory required.")
    st = folder.stat()
    _require(st.st_uid == os.geteuid() and st.st_mode & 0o077 == 0,
             "Output folder must be mode 0700 and user owned.")
    name = "candidate-" + record["provider"] + "-" + record["run_id"]
    manifest_path = folder / (name + ".json")
    archive_path = folder / (name + ".zip")
    _require(not manifest_path.exists() and not archive_path.exists(),
             "Candidate output already exists; do not overwrite.")
    for path, data in ((archive_path, content),
                       (manifest_path, json.dumps(record, sort_keys=True,
                                                  indent=2).encode("utf-8"))):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
    return {"unsigned_candidate": str(manifest_path),
            "release_zip": str(archive_path),
            "sha256": record["artifact_sha256"],
            "deployable": False}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--provider", choices=("github", "gitlab"), required=True)
    p.add_argument("--repository", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--ref", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--check", action="append", required=True)
    p.add_argument("--workflow-id", type=int, default=None)
    p.add_argument("--artifact-job", default=None)
    p.add_argument("--output-dir", required=True)
    a = p.parse_args(argv)
    try:
        if a.provider == "github":
            rec, data = verify_github(a.repository, a.run_id, a.sha, a.ref,
                                      a.actor, a.check, workflow_id=a.workflow_id)
        else:
            rec, data = verify_gitlab(a.repository, a.run_id, a.sha, a.ref,
                                      a.actor, a.check, artifact_job=a.artifact_job)
        print(json.dumps(write_unsigned_evidence(a.output_dir, rec, data), indent=2))
        return 0
    except (OSError, ValueError, PermissionError, BadZipFile,
            subprocess.TimeoutExpired, TypeError) as error:
        print("Provider evidence denied: " + str(error)[:220], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
