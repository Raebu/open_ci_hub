#!/usr/bin/env python3
"""Opt-in, path-scoped coding workspace backend for the owner-local MCP server.

No arbitrary remote shell: the owner enrols directories, write rights and exact
check commands locally. Checks run as the normal Linux user, NOT in a sandbox.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import time

from hub import secure_write

ROOT = Path(__file__).resolve().parent
REGISTRY = ROOT / ".local" / "direct-mcp" / "workspaces.json"
RECEIPTS = ROOT / ".local" / "direct-mcp" / "coding-receipts.jsonl"
NAME = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
CHECK = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
SHA = re.compile(r"^[a-f0-9]{64}$")
BLOCKED = frozenset({".git", ".local", ".ssh", ".aws", ".gnupg", "node_modules",
                     "vendor", "__pycache__", ".next", ".venv", "venv", ".env",
                     ".env.local", "id_rsa", "id_ed25519", "credentials.json"})
MAX_FILE = 65536
MAX_RESULTS = 150
MAX_CHECK_SECONDS = 180


def _require_user():
    if os.name != "posix" or os.geteuid() == 0:
        raise PermissionError("Workspace MCP must run as an ordinary Linux user.")


def _registry():
    _require_user()
    if not REGISTRY.exists():
        return {}
    if REGISTRY.is_symlink() or REGISTRY.parent.is_symlink():
        raise PermissionError("Unsafe workspace registry.")
    st = REGISTRY.stat()
    if st.st_uid != os.geteuid() or st.st_mode & 0o077:
        raise PermissionError("Workspace registry must be private and owner-owned.")
    entries = json.loads(REGISTRY.read_text("utf-8"))
    if not isinstance(entries, dict) or any(not NAME.fullmatch(k) or
        not isinstance(v, dict) or set(v) != {"root", "write", "checks"} or
        type(v["write"]) is not bool or not isinstance(v["checks"], dict)
        for k, v in entries.items()):
        raise PermissionError("Malformed workspace registry.")
    return entries


def _save(entries):
    REGISTRY.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    secure_write(REGISTRY, json.dumps(entries, indent=2, sort_keys=True) + "\n")


def _root(workspace):
    if not isinstance(workspace, str) or not NAME.fullmatch(workspace):
        raise ValueError("Invalid enrolled workspace name.")
    entry = _registry().get(workspace)
    if entry is None:
        raise PermissionError("Workspace is not enrolled.")
    root = Path(entry["root"])
    if (not root.is_absolute() or root.is_symlink() or root.resolve() != root or
        not root.is_dir() or root == Path.home().resolve() or root == Path("/")):
        raise PermissionError("Workspace directory is missing or changed.")
    return root, entry


def _parts(relative):
    if (not isinstance(relative, str) or len(relative) > 300 or not relative or
        relative.startswith("/") or "\x00" in relative or "\\" in relative):
        raise ValueError("Invalid relative path.")
    segments = relative.split("/")
    if any(part in ("", ".", "..") or part.startswith(".") or
           part.lower() in BLOCKED for part in segments):
        raise PermissionError("Hidden, sensitive or traversal paths are not accessible.")
    return segments


def _directory(root, segments):
    """Return fd of directory reached without traversing any symlink."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in segments:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def workspaces():
    result = []
    for name in sorted(_registry()):
        try:
            root, entry = _root(name)
            result.append({"name": name, "root": str(root),
                           "write": entry["write"],
                           "checks": sorted(entry["checks"])})
        except (OSError, ValueError, PermissionError):
            result.append({"name": name, "status": "unavailable"})
    return {"workspaces": result}


def list_files(workspace, path=""):
    root, _ = _root(workspace)
    segments = _parts(path) if path else []
    fd = _directory(root, segments)
    try:
        items = []
        for name in sorted(os.listdir(fd)):
            if (name.startswith(".") or name.lower() in BLOCKED or
                "/" in name or "\\" in name):
                continue
            try:
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode):
                items.append({"name": name,
                              "kind": "directory" if stat.S_ISDIR(st.st_mode) else "file",
                              "bytes": st.st_size if stat.S_ISREG(st.st_mode) else None})
            if len(items) >= MAX_RESULTS:
                break
        return {"workspace": workspace, "path": path, "entries": items,
                "truncated": len(items) >= MAX_RESULTS}
    finally:
        os.close(fd)


def _read_fd(parent_fd, basename):
    fd = os.open(basename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE:
            raise PermissionError("Only regular files up to 64 KiB can be read.")
        data = bytearray()
        while len(data) <= MAX_FILE:
            chunk = os.read(fd, min(8192, MAX_FILE + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > MAX_FILE or b"\x00" in data:
            raise PermissionError("Binary or oversized files are not exposed.")
        return bytes(data)
    finally:
        os.close(fd)


def read_file(workspace, path):
    root, _ = _root(workspace)
    segments = _parts(path)
    fd = _directory(root, segments[:-1])
    try:
        raw = _read_fd(fd, segments[-1])
        return {"workspace": workspace, "path": path,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "content": raw.decode("utf-8")}
    finally:
        os.close(fd)


def _receipt(payload):
    RECEIPTS.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    record = {"id": secrets.token_hex(12), "time": int(time.time()), **payload}
    fd = os.open(RECEIPTS, os.O_APPEND | os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        st = os.fstat(fd)
        if st.st_uid != os.geteuid() or st.st_mode & 0o077:
            raise PermissionError("Unsafe audit journal.")
        os.write(fd, (json.dumps(record, sort_keys=True) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    return record["id"]


def write_file(workspace, path, content, expected_sha256=""):
    root, entry = _root(workspace)
    if not entry["write"]:
        raise PermissionError("Owner has not enabled writes for this workspace.")
    segments = _parts(path)
    if (not isinstance(content, str) or "\x00" in content or
        len(content.encode("utf-8")) > MAX_FILE):
        raise ValueError("Only UTF-8 text up to 64 KiB is allowed.")
    if expected_sha256 and (not isinstance(expected_sha256, str) or
                            not SHA.fullmatch(expected_sha256)):
        raise ValueError("Expected SHA-256 must be a full lowercase hash.")
    payload = content.encode("utf-8")
    root_fd = _directory(root, [])
    try:
        fcntl.flock(root_fd, fcntl.LOCK_EX)
        parent_fd = _directory(root, segments[:-1])
        try:
            try:
                previous = _read_fd(parent_fd, segments[-1])
                previous_mode = os.stat(segments[-1], dir_fd=parent_fd,
                                        follow_symlinks=False).st_mode & 0o777
                exists = True
            except FileNotFoundError:
                previous, previous_mode, exists = b"", 0o600, False
            if exists and hashlib.sha256(previous).hexdigest() != expected_sha256:
                raise PermissionError("File changed or missing expected SHA-256.")
            if not exists and expected_sha256:
                raise PermissionError("Expected existing file does not exist.")
            if len(payload) == 0 and not exists:
                raise ValueError("Do not create empty new files.")
            temp = ".mcp-" + secrets.token_hex(16)
            tmpfd = os.open(temp, os.O_WRONLY | os.O_EXCL | os.O_CREAT | os.O_NOFOLLOW,
                            previous_mode, dir_fd=parent_fd)
            try:
                try:
                    with os.fdopen(tmpfd, "wb") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temp, segments[-1], src_dir_fd=parent_fd,
                               dst_dir_fd=parent_fd)
                    os.fsync(parent_fd)
                except BaseException:
                    try:
                        os.unlink(temp, dir_fd=parent_fd)
                    except FileNotFoundError:
                        pass
                    raise
            except BaseException:
                raise
            verified = _read_fd(parent_fd, segments[-1])
            digest = hashlib.sha256(verified).hexdigest()
            if verified != payload:
                raise RuntimeError("Write verification mismatch.")
        finally:
            os.close(parent_fd)
    finally:
        os.close(root_fd)
    receipt = _receipt({"operation": "write", "workspace": workspace, "path": path,
                        "sha256": digest, "previous_sha256":
                        hashlib.sha256(previous).hexdigest() if exists else None})
    return {"result": "verified", "workspace": workspace, "path": path,
            "bytes": len(payload), "sha256": digest, "receipt": receipt}


def run_check(workspace, check):
    root, entry = _root(workspace)
    if not isinstance(check, str) or not CHECK.fullmatch(check):
        raise ValueError("Invalid check name.")
    argv = entry["checks"].get(check)
    if not isinstance(argv, list) or not argv or any(
        not isinstance(a, str) or not a or "\x00" in a for a in argv
    ):
        raise PermissionError("Check is not locally authorised.")
    # Tests execute repository code as the ordinary user. This is not isolation.
    env = {k: v for k, v in os.environ.items() if k in
           ("PATH", "HOME", "LANG", "LC_ALL", "XDG_RUNTIME_DIR")}
    env["CI"] = "1"
    started = time.monotonic()
    try:
        result = subprocess.run(argv, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=MAX_CHECK_SECONDS, check=False)
        output = result.stdout[:32768]
        status, exit_code = ("passed" if result.returncode == 0 else "failed"), result.returncode
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b"")[:32768]
        status, exit_code = "timed_out", None
    # No raw output is returned: test logs may contain confidential data or credentials.
    receipt = _receipt({"operation": "check", "workspace": workspace, "check": check,
                        "status": status, "exit_code": exit_code,
                        "argv_sha256": hashlib.sha256(json.dumps(argv).encode()).hexdigest(),
                        "output_sha256": hashlib.sha256(output).hexdigest(),
                        "duration_ms": round((time.monotonic() - started) * 1000)})
    return {"workspace": workspace, "check": check, "status": status,
            "exit_code": exit_code, "duration_ms": round((time.monotonic() - started) * 1000),
            "output_sha256": hashlib.sha256(output).hexdigest(),
            "output_bytes_captured": len(output), "receipt": receipt}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Locally enrol coding workspaces and checks")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("status")
    enrol = sub.add_parser("enrol")
    enrol.add_argument("name")
    enrol.add_argument("path")
    enrol.add_argument("--confirm", action="store_true")
    enable = sub.add_parser("enable-writes")
    enable.add_argument("name")
    enable.add_argument("--confirm", action="store_true")
    check = sub.add_parser("allow-check")
    check.add_argument("name")
    check.add_argument("check")
    check.add_argument("argv", nargs=argparse.REMAINDER)
    check.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    _require_user()
    if args.action == "status":
        print(json.dumps(workspaces(), indent=2))
        return 0
    if not args.confirm:
        raise PermissionError("Local owner must pass --confirm.")
    entries = _registry()
    if args.action == "enrol":
        if not NAME.fullmatch(args.name) or args.name in entries:
            raise ValueError("Invalid or existing workspace name.")
        path = Path(args.path)
        if not path.is_absolute() or path.is_symlink() or path.resolve() != path or not path.is_dir():
            raise PermissionError("Enrol an existing canonical local directory.")
        if path == Path.home().resolve() or path == Path("/") or str(path).startswith(
            ("/etc/", "/proc/", "/sys/", "/dev/", "/var/", "/usr/", "/run/")
        ):
            raise PermissionError("Refusing broad or system workspaces.")
        entries[args.name] = {"root": str(path), "write": False, "checks": {}}
    elif args.action == "enable-writes":
        if args.name not in entries:
            raise ValueError("Workspace not enrolled.")
        entries[args.name]["write"] = True
    elif args.action == "allow-check":
        if args.name not in entries or not CHECK.fullmatch(args.check):
            raise ValueError("Invalid workspace or check name.")
        commands = args.argv
        if commands and commands[0] == "--":
            commands = commands[1:]
        if not 1 <= len(commands) <= 15 or any(
            not c or len(c) > 200 or "\x00" in c for c in commands
        ) or commands[0] in ("bash", "sh", "sudo", "su"):
            raise PermissionError("Only explicit bounded command arrays are permitted.")
        entries[args.name]["checks"][args.check] = commands
    _save(entries)
    print(json.dumps({"updated": True, "workspaces": list(entries)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
