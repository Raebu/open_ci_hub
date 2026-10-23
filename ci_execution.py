"""Bounded, redacted local-container output and confirmed termination.

Workspace size checks are a periodic safety limit, not filesystem quotas.
No resource reservation may be released following an uncertain Docker kill.
"""
from __future__ import annotations
import os
from pathlib import Path
import selectors
import subprocess
import time

MAX_LOG_BYTES = 4 * 1024 * 1024
MAX_LINE_BYTES = 16 * 1024


def redact(text):
    from direct_pc_mcp import _redact
    text = _redact(text)[0]
    # GitLab tokens and common assignment variants are not in the older PC filter.
    import re
    text = re.sub(r'\bglpat-[A-Za-z0-9_-]{10,}\b', '[REDACTED TOKEN]', text)
    text = re.sub(r'(?i)(["\x27]?(?:token|secret|password|api[_-]?key)["\x27]?\s*[:=]\s*)[^\s,}]+',
                  r'\1[REDACTED]', text)
    return text


def workspace_bytes(root, limit):
    """Never follow repository symlinks. Fail closed on unavailable storage."""
    amount = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
        for name in files:
            path = Path(directory) / name
            if path.is_symlink():
                continue
            amount += path.stat().st_size
            if amount > limit:
                return amount
    return amount


def stop_container(name, run):
    """A kill request alone is insufficient evidence that the container stopped."""
    try:
        run(['docker', 'kill', name], timeout=8)
        probe = run(['docker', 'inspect', '--format', '{{.State.Running}}', name], timeout=8)
        if probe.returncode == 0:
            return probe.stdout.strip() == 'false'
        # --rm containers disappear; verify exact name absence, not generic inspect failure.
        probe = run(['docker', 'ps', '-aq', '--filter', 'name=^/'+name+'$'], timeout=8)
        return probe.returncode == 0 and not probe.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False


def run_container(argv, *, cwd, timeout, log_path, heartbeat, container_name,
                  run, cancelled=lambda: False, workspace=None, max_workspace_mb=2048,
                  heartbeat_seconds=45):
    deadline = time.monotonic() + timeout
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if log_path.is_symlink():
        raise PermissionError('CI log cannot be a symlink')
    fd = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    proc = None
    uncertain = False
    reason = None
    pending = bytearray()
    dropping = False
    private_key = False
    seen = 0
    with os.fdopen(fd, 'ab', buffering=0) as stream:
        if log_path.stat().st_size >= MAX_LOG_BYTES:
            return 122, False
        proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True)
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        next_heartbeat = time.monotonic() + heartbeat_seconds
        next_disk = 0
        try:
            while True:
                now = time.monotonic()
                if cancelled():
                    reason = 'cancelled'; break
                if now >= deadline:
                    reason = 'timeout'; break
                if now >= next_heartbeat:
                    if not heartbeat():
                        reason = 'lease_lost'; break
                    next_heartbeat = now + heartbeat_seconds
                if workspace is not None and now >= next_disk:
                    if not Path(workspace).is_dir():
                        reason = 'storage_unavailable'; break
                    if workspace_bytes(workspace, max_workspace_mb * 1024 * 1024) > max_workspace_mb * 1024 * 1024:
                        reason = 'workspace_limit'; break
                    next_disk = now + 2
                ready = selector.select(0.2)
                if ready:
                    chunk = os.read(proc.stdout.fileno(), 8192)
                    if not chunk:
                        break
                    seen += len(chunk)
                    if seen + stream.tell() > MAX_LOG_BYTES - 1024:
                        reason = 'output_limit'; break
                    for part in chunk.splitlines(keepends=True):
                        if not dropping:
                            pending.extend(part)
                            if len(pending) > MAX_LINE_BYTES:
                                pending.clear(); dropping = True
                        if part.endswith((b'\n', b'\r')):
                            if b'-----BEGIN ' in pending and b'PRIVATE KEY-----' in pending:
                                private_key = True
                            end_private = b'-----END ' in pending and b'PRIVATE KEY-----' in pending
                            if private_key:
                                if end_private:
                                    private_key=False
                                stream.write(b'[REDACTED PRIVATE KEY]\n')
                            elif dropping:
                                stream.write(b'[Open CI Hub: oversized log line omitted]\n')
                            else:
                                stream.write(redact(pending.decode('utf-8', 'replace')).encode())
                            pending.clear(); dropping = False
                elif proc.poll() is not None:
                    # Drain until pipe EOF; select will become readable.
                    continue
            if reason:
                uncertain = not stop_container(container_name, run)
                if proc.poll() is None:
                    proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait(timeout=5)
                stream.write(('\nOpen CI Hub: '+reason+'; termination '+
                              ('UNCONFIRMED' if uncertain else 'confirmed')+'.\n').encode())
                return {'cancelled':130, 'timeout':124, 'lease_lost':125,
                        'output_limit':122, 'workspace_limit':123,
                        'storage_unavailable':126}[reason], uncertain or reason == 'lease_lost'
            if pending and not dropping and not private_key:
                stream.write(redact(pending.decode('utf-8', 'replace')).encode())
            try:
                return proc.wait(timeout=5), False
            except subprocess.TimeoutExpired:
                return 125, not stop_container(container_name, run)
        except Exception:
            stop_container(container_name, run)
            if proc.poll() is None:
                proc.kill(); proc.wait(timeout=5)
            stream.write(b'\nOpen CI Hub: monitor failure; lease retained for recovery.\n')
            return 125, True
        except BaseException:
            stop_container(container_name, run)
            if proc.poll() is None:
                proc.kill(); proc.wait(timeout=5)
            raise
        finally:
            selector.close()
            proc.stdout.close()
