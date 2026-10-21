#!/usr/bin/env python3
"""Opt-in, non-root release staging and rollback for user-managed PC apps.

No unattended deployment or CI trust is granted by this module. The owner
enrols an app, verifies an exact archive digest and explicitly confirms
promotion. A separate authenticated release coordinator must verify CI
provenance before any future automatic promotion.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from zipfile import ZipFile, BadZipFile

from hub import secure_write

ROOT = Path.home() / ".local" / "share" / "open-ci-hub" / "pc-releases"
APPS = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_EXTRACT_BYTES = 256 * 1024 * 1024
MAX_FILES = 5000
MAX_DEPTH = 20


def _owner():
    if os.name != "posix" or os.geteuid() == 0:
        raise PermissionError("PC deployments run as the normal Linux user, not root.")


def _validate_name(name):
    if not isinstance(name, str) or not APPS.fullmatch(name):
        raise ValueError("Invalid application identifier.")
    return name


def _app_dir(name):
    return ROOT / _validate_name(name)


def _private_file(path):
    if path.is_symlink() or not path.is_file():
        raise PermissionError("Missing or symlinked private state.")
    st = path.stat()
    if st.st_uid != os.geteuid() or st.st_mode & 0o077:
        raise PermissionError("Private state must be owned by the current user and mode 0600.")
    return json.loads(path.read_text("utf-8"))


def _config(name):
    folder = _app_dir(name)
    if folder.is_symlink() or not folder.is_dir():
        raise PermissionError("Application is not locally enrolled.")
    data = _private_file(folder / "config.json")
    if (not isinstance(data, dict) or set(data) !=
            {"schema", "name", "service", "health_port"} or
            data["schema"] != "open-ci-hub.pc-release-config.v1" or data["name"] != name or
            not isinstance(data["service"], str) or
            data["service"] not in ("", "open-ci-hub-app-" + name + ".service") or
            type(data["health_port"]) is not int or
            not 0 <= data["health_port"] <= 65535):
        raise PermissionError("Invalid enrolled deployment configuration.")
    return data


def _lock(folder):
    import contextlib
    @contextlib.contextmanager
    def held():
        if folder.is_symlink() or not folder.is_dir():
            raise PermissionError("Application folder is unsafe.")
        path = folder / "release.lock"
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            st = os.fstat(fd)
            if st.st_uid != os.geteuid() or st.st_mode & 0o077:
                raise PermissionError("Unsafe release lock ownership.")
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)
    return held()


def _path(name):
    folder = _app_dir(name)
    releases = folder / "releases"
    if releases.is_symlink() or not releases.is_dir():
        raise PermissionError("Missing or unsafe release directory.")
    return folder, releases


def _check_link(folder, releases):
    current = folder / "current"
    if not current.is_symlink():
        if current.exists():
            raise PermissionError("Current release must be a managed symlink.")
        return None
    target = os.readlink(current)
    if (not isinstance(target, str) or not re.fullmatch(r"releases/[0-9a-f]{40}", target)
        or not (releases / target.rsplit("/", 1)[1]).is_dir()
        or (releases / target.rsplit("/", 1)[1]).is_symlink()):
        raise PermissionError("Current release pointer was externally modified.")
    return target.rsplit("/", 1)[1]


def _manifest(folder, sha):
    if not SHA40.fullmatch(sha):
        raise ValueError("Full 40-character source commit SHA required.")
    releases = folder / "releases"
    source = releases / sha
    if source.is_symlink() or not source.is_dir():
        raise PermissionError("Release was not staged.")
    data = _private_file(source / "release.json")
    if (not isinstance(data, dict) or set(data) !=
        {"schema", "commit", "archive_sha256", "file_count", "total_bytes"} or
        data["schema"] != "open-ci-hub.pc-release.v1" or data["commit"] != sha or
        not isinstance(data["archive_sha256"], str) or
        not SHA256.fullmatch(data["archive_sha256"])):
        raise PermissionError("Invalid staged release metadata.")
    return data


def _hash_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _archive_entries(archive):
    paths = set()
    total = 0
    files = 0
    members = []
    for info in archive.infolist():
        raw = info.filename
        if (not raw or "\\" in raw or "\x00" in raw or raw.startswith("/")
            or len(raw) > 300):
            raise PermissionError("Unsafe ZIP member name.")
        is_dir = info.is_dir()
        value = raw[:-1] if is_dir else raw
        parts = value.split("/")
        if (len(parts) > MAX_DEPTH or any(p in ("", ".", "..") or
            p.startswith(".") for p in parts)
            or any(ord(ch) < 32 for ch in value)):
            raise PermissionError("Unsafe ZIP path traversal or hidden member.")
        canonical = "/".join(parts)
        if canonical in paths or canonical == "release.json":
            raise PermissionError("Duplicate file or reserved release metadata.")
        paths.add(canonical)
        mode = (info.external_attr >> 16) & 0xFFFF
        ftype = stat.S_IFMT(mode)
        if ftype not in (0, stat.S_IFDIR if is_dir else stat.S_IFREG):
            raise PermissionError("ZIP contains a symlink or unsupported file type.")
        if not is_dir:
            files += 1
            total += info.file_size
            if info.file_size > MAX_EXTRACT_BYTES or total > MAX_EXTRACT_BYTES:
                raise PermissionError("Archive expands beyond disk quota.")
        if files > MAX_FILES or len(paths) > MAX_FILES * 2:
            raise PermissionError("Too many ZIP members.")
        members.append((info, parts, is_dir))
    if not files:
        raise ValueError("Release archive contains no application files.")
    # Reject archive entries that are simultaneously a file and a parent path.
    file_paths = {"/".join(parts) for _, parts, d in members if not d}
    for _, parts, _ in members:
        for n in range(1, len(parts)):
            if "/".join(parts[:n]) in file_paths:
                raise PermissionError("ZIP file conflicts with a directory.")
    return members, files, total


def _copy_archive(archive_path, destination):
    with ZipFile(archive_path, "r") as archive:
        members, files, total = _archive_entries(archive)
        for info, parts, is_dir in members:
            path = destination.joinpath(*parts)
            if is_dir:
                path.mkdir(parents=True, exist_ok=True, mode=0o700)
                continue
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.exists() or path.is_symlink():
                raise PermissionError("Duplicate output file.")
            with archive.open(info, "r") as source, open(path, "xb") as output:
                copied = 0
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > info.file_size or copied > MAX_EXTRACT_BYTES:
                        raise PermissionError("ZIP expanded beyond declared size.")
                    output.write(chunk)
                if copied != info.file_size:
                    raise PermissionError("ZIP member size mismatch.")
                output.flush()
                os.fsync(output.fileno())
            os.chmod(path, 0o600)
    return files, total


def enrol(name, *, service=False, health_port=0, confirm=False):
    _owner()
    _validate_name(name)
    if confirm is not True:
        raise PermissionError("Initial enrolment requires the PC owner's --confirm.")
    if type(service) is not bool or type(health_port) is not int or not 0 <= health_port <= 65535:
        raise ValueError("Invalid systemd service or local health-check port.")
    folder = _app_dir(name)
    if folder.exists() or folder.is_symlink():
        raise FileExistsError("Application is already enrolled; do not overwrite existing state.")
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    if ROOT.is_symlink():
        raise PermissionError("Managed deployment root may not be a symlink.")
    folder.mkdir(mode=0o700)
    (folder / "releases").mkdir(mode=0o700)
    conf = {"schema": "open-ci-hub.pc-release-config.v1", "name": name,
            "service": "open-ci-hub-app-" + name + ".service" if service else "",
            "health_port": health_port}
    secure_write(folder / "config.json", json.dumps(conf, sort_keys=True) + "\n")
    return {"app": name, "enrolled": True, "service": conf["service"],
            "health_port": health_port, "automatic_production": False}


def stage(name, *, archive, commit, expected_sha256):
    _owner()
    _config(name)
    if not isinstance(commit, str) or not SHA40.fullmatch(commit):
        raise ValueError("Exact source commit SHA required.")
    if not isinstance(expected_sha256, str) or not SHA256.fullmatch(expected_sha256):
        raise ValueError("Exact artifact archive SHA-256 required.")
    source = Path(archive)
    if (not source.is_absolute() or source.is_symlink() or not source.is_file()
        or source.stat().st_size > MAX_ARCHIVE_BYTES):
        raise PermissionError("Artifact must be an existing small local regular ZIP.")
    folder, releases = _path(name)
    with _lock(folder):
        if _hash_file(source) != expected_sha256:
            raise PermissionError("Artifact SHA-256 differs from expected manifest.")
        final = releases / commit
        if final.exists() or final.is_symlink():
            data = _manifest(folder, commit)
            if data["archive_sha256"] != expected_sha256:
                raise PermissionError("Existing commit has a different artifact.")
            return {"app": name, "commit": commit, "status": "already_staged",
                    "archive_sha256": expected_sha256}
        temp = Path(tempfile.mkdtemp(prefix=".staging-", dir=releases))
        try:
            files, total = _copy_archive(source, temp)
            secure_write(temp / "release.json", json.dumps({
                "schema": "open-ci-hub.pc-release.v1", "commit": commit,
                "archive_sha256": expected_sha256, "file_count": files,
                "total_bytes": total}, sort_keys=True) + "\n")
            os.replace(temp, final)
        finally:
            if temp.exists():
                shutil.rmtree(temp)
        return {"app": name, "commit": commit, "status": "staged",
                "archive_sha256": expected_sha256, "files": files, "bytes": total}


def _switch(folder, target):
    link = folder / "current"
    if target is None:
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            raise PermissionError("Current pointer isn't a symlink.")
        return
    temp = folder / (".current-" + os.urandom(10).hex())
    try:
        temp.symlink_to("releases/" + target)
        if link.exists() and not link.is_symlink():
            raise PermissionError("Refusing to overwrite unmanaged current path.")
        os.replace(temp, link)
    finally:
        if temp.is_symlink():
            temp.unlink()


def _restart(service):
    if not service:
        return
    result = subprocess.run(["/usr/bin/systemctl", "--user", "restart", service],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=25, check=False)
    if result.returncode:
        raise RuntimeError("Managed user service could not be restarted.")


def _healthy(port):
    if not port:
        return True
    for _ in range(5):
        try:
            with urlopen("http://127.0.0.1:" + str(port) + "/health", timeout=2) as response:
                if 200 <= response.status < 300:
                    return True
        except (OSError, ValueError):
            pass
        time.sleep(1)
    return False


def status(name):
    _owner()
    config = _config(name)
    folder, releases = _path(name)
    with _lock(folder):
        return {"app": name, "current_commit": _check_link(folder, releases),
                "staged": sorted(p.name for p in releases.iterdir()
                    if p.is_dir() and not p.is_symlink() and SHA40.fullmatch(p.name)),
                "service": config["service"], "health_port": config["health_port"]}


def promote(name, commit, *, confirm=False):
    _owner()
    config = _config(name)
    if confirm is not True:
        raise PermissionError("Production promotion requires local owner confirmation.")
    folder, releases = _path(name)
    with _lock(folder):
        _manifest(folder, commit)
        before = _check_link(folder, releases)
        if before == commit:
            return {"app": name, "commit": commit, "status": "already_current"}
        _switch(folder, commit)
        try:
            _restart(config["service"])
            if not _healthy(config["health_port"]):
                raise RuntimeError("Promoted app did not pass its loopback health check.")
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            _switch(folder, before)
            recovery = "restored"
            try:
                _restart(config["service"])
                if before is not None and not _healthy(config["health_port"]):
                    recovery = "restored_pointer_health_unverified"
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                recovery = "restored_pointer_restart_failed"
            raise RuntimeError("Promotion aborted; previous pointer " + recovery) from error
        secure_write(folder / "previous.json",
                     json.dumps({"previous_commit": before, "current_commit": commit}) + "\n")
        return {"app": name, "commit": commit, "previous_commit": before,
                "status": "promoted", "health": "passed" if config["health_port"] else "not_configured"}


def rollback(name, *, confirm=False):
    _owner()
    config = _config(name)
    if confirm is not True:
        raise PermissionError("Manual rollback requires local owner confirmation.")
    folder, releases = _path(name)
    with _lock(folder):
        record = _private_file(folder / "previous.json")
        if not isinstance(record, dict) or set(record) != {"previous_commit", "current_commit"}:
            raise PermissionError("Invalid previous release record.")
        before, active = record["previous_commit"], record["current_commit"]
        if before is not None:
            _manifest(folder, before)
        current = _check_link(folder, releases)
        if current != active or current == before:
            raise PermissionError("Cannot roll back an externally changed release.")
        _switch(folder, before)
        try:
            _restart(config["service"])
            if before is not None and not _healthy(config["health_port"]):
                raise RuntimeError("Rollback health check failed.")
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            _switch(folder, active)
            try:
                _restart(config["service"])
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                pass
            raise RuntimeError("Rollback failed; active pointer restored.") from error
        secure_write(folder / "previous.json",
                     json.dumps({"previous_commit": active, "current_commit": before}) + "\n")
        return {"app": name, "status": "rolled_back", "current_commit": before,
                "from_commit": active}


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest="action", required=True)
    p = sub.add_parser("enrol")
    p.add_argument("name")
    p.add_argument("--service", action="store_true")
    p.add_argument("--health-port", type=int, default=0)
    p.add_argument("--confirm", action="store_true")
    p = sub.add_parser("stage")
    p.add_argument("name")
    p.add_argument("--archive", required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--sha256", required=True)
    for action in ("status", "promote", "rollback"):
        p = sub.add_parser(action)
        p.add_argument("name")
        if action == "promote":
            p.add_argument("commit")
        if action != "status":
            p.add_argument("--confirm", action="store_true")
    a = cli.parse_args(argv)
    try:
        if a.action == "enrol":
            result = enrol(a.name, service=a.service, health_port=a.health_port, confirm=a.confirm)
        elif a.action == "stage":
            result = stage(a.name, archive=a.archive, commit=a.commit, expected_sha256=a.sha256)
        elif a.action == "status":
            result = status(a.name)
        elif a.action == "promote":
            result = promote(a.name, a.commit, confirm=a.confirm)
        else:
            result = rollback(a.name, confirm=a.confirm)
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    except (OSError, ValueError, PermissionError, RuntimeError, BadZipFile,
            subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        print("PC release refused: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
