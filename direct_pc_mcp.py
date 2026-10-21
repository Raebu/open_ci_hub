#!/usr/bin/env python3
"""Owner-local MCP endpoint for bounded, explicitly enrolled Garuda PC operations.

The Secure MCP Tunnel authenticates its control-plane connection, but this
stdio server does not receive a cryptographic signature over the original
ChatGPT message or a verified end-user identity. Powerful capabilities remain
disabled until the local PC owner explicitly enrols them.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlparse
from urllib.request import urlopen

from hub import secure_write

ROOT = Path(__file__).resolve().parent
STATE_ROOT = ROOT / ".local"
STATE_DIR = STATE_ROOT / "direct-mcp"
PERMISSIONS = STATE_DIR / "permissions.json"
AUDIT = STATE_DIR / "audit.jsonl"
SCHEMA = "open-ci-hub.direct-pc-consent.v2"
LEGACY_SCHEMA = "open-ci-hub.direct-pc-consent.v1"
VERSION = "2025-06-18"

CAPABILITIES = (
    "document_creation",
    "file_browse",
    "file_read",
    "file_edit",
    "terminal_execution",
    "software_install",
    "desktop_control",
)
CAPABILITY_COMMANDS = {
    "document_creation": "enable-documents --confirm",
    "file_browse": "enable-file-access --confirm",
    "file_read": "enable-file-access --confirm",
    "file_edit": "enable-file-access --confirm",
    "terminal_execution": "enable-terminal --confirm",
    "software_install": "enable-software-install --confirm",
    "desktop_control": "enable-desktop-control --confirm",
}
DOC_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\.(?:md|txt)$")
PACKAGE_NAME = re.compile(r"^[A-Za-z0-9@._+-]{1,80}$")
WINDOW_ID = re.compile(r"^\{[0-9a-fA-F-]{36}\}$")
MAX_INPUT_BYTES = 1_100_000
MAX_DOC_BYTES = 4_000
MAX_FILE_BYTES = 256 * 1024
MAX_COMMAND_CHARS = 8_000
MAX_COMMAND_SECONDS = 300
MAX_INSTALL_SECONDS = 600
MAX_OUTPUT_BYTES = 16 * 1024
MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024
MAX_DIRECTORY_ENTRIES = 120

BLOCKED_DIRS = {
    ".git", ".ssh", ".gnupg", ".config", ".local", ".aws", ".azure",
    ".kube", ".pki", ".password-store", "keyrings", "node_modules",
    ".venv", "venv", "__pycache__", ".cache", ".npm", ".cargo",
}
SAFE_ENV_EXAMPLE_NAMES = {".env.example", ".env.sample", ".env.template"}
SENSITIVE_BASENAMES = {
    ".netrc", ".npmrc", ".pypirc", ".git-credentials", "id_rsa",
    "id_ed25519", "authorized_keys", "known_hosts",
}
TEXT_EXTENSIONS = {
    ".py", ".pyi", ".md", ".rst", ".txt", ".json", ".jsonc", ".toml",
    ".yaml", ".yml", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".css", ".scss", ".html", ".htm", ".xml", ".svg", ".sql", ".sh",
    ".fish", ".conf", ".ini", ".cfg", ".properties", ".csv", ".log",
    ".lock", ".mod", ".sum", ".go", ".rs", ".java", ".kt", ".swift",
    ".php", ".rb", ".pl", ".ps1", ".bat", ".cmd", ".service", ".desktop",
    ".ipynb", ".vue", ".svelte", ".graphql", ".gql", ".make", ".mk",
}
TEXT_BASENAMES = {
    "Dockerfile", "Containerfile", "Makefile", "GNUmakefile", "Justfile",
    "Procfile", "LICENSE", "NOTICE", "COPYING", "CHANGELOG", "AUTHORS",
    "CONTRIBUTORS", "MANIFEST.in", ".gitignore", ".dockerignore",
    ".editorconfig", ".gitattributes", ".prettierrc", ".eslintrc",
    ".npmrc.example",
}
SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.S),
     "[REDACTED PRIVATE KEY]"),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|AKIA[0-9A-Z]{16})\b"),
     "[REDACTED TOKEN]"),
    (re.compile(r"(?im)^(\s*(?:api[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|password|passwd|secret)\s*[:=]\s*).+$"),
     r"\1[REDACTED]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)\b(https?://)[^/\s@]+@"), r"\1[REDACTED]@"),
]

TOOLS = [
    {
        "name": "get_pc_status",
        "description": "Read live Garuda system resources, Open CI Hub service health, enabled local capabilities and approved browsing roots. Read-only.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "inspect_ci_failure",
        "description": "Read native GitHub Actions/GitLab CI job and redacted failure evidence for a named run. Never retries or modifies workflows.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {"type": "string", "enum": ["github", "gitlab"]},
                "repository": {"type": "string", "minLength": 3, "maxLength": 300},
                "run_id": {"type": "string", "pattern": r"^[0-9]{1,18}$"},
            },
            "required": ["provider", "repository", "run_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_pc_document",
        "description": "Create a new Markdown/text document up to 4,000 bytes in ~/Documents/OpenCIHubTasks. Never overwrites. Requires local owner enrolment.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "pattern": DOC_NAME.pattern},
                "content": {"type": "string", "minLength": 1, "maxLength": MAX_DOC_BYTES},
            },
            "required": ["filename", "content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_pc_directory",
        "description": "List an approved Documents, Downloads, Desktop or Code_Projects directory. Hides secret/config directories. Empty path lists the approved roots.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "maxLength": 1024}},
            "additionalProperties": False,
        },
    },
    {
        "name": "read_pc_file",
        "description": "Read a UTF-8 text file in an approved local folder. Secret/config paths are blocked, file size is bounded and likely credentials are redacted.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "minLength": 1, "maxLength": 1024}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "write_pc_file",
        "description": "Create or edit a text/source file within Code_Projects only. Existing files require their current SHA-256 to prevent overwriting concurrent changes; a private backup is made before replacement.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "minLength": 1, "maxLength": 1024},
                "content": {"type": "string", "maxLength": MAX_FILE_BYTES},
                "expected_sha256": {"type": ["string", "null"], "pattern": "^[a-f0-9]{64}$"},
            },
            "required": ["path", "content", "expected_sha256"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_pc_command",
        "description": "Run a requested command as the ordinary Garuda desktop user in an approved Code_Projects directory. Bounded to 300 seconds and 16 KiB of output; never runs this MCP process as root. Requires local owner enrolment.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "minLength": 1, "maxLength": MAX_COMMAND_CHARS},
                "cwd": {"type": "string", "maxLength": 1024},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": MAX_COMMAND_SECONDS},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
    {
        "name": "install_arch_packages",
        "description": "Install named packages from official Arch/Garuda repositories using pkexec and pacman only. KDE may display its normal authentication prompt. Does not run AUR build scripts or bypass polkit. Requires local owner enrolment.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "packages": {
                    "type": "array", "minItems": 1, "maxItems": 10,
                    "items": {"type": "string", "pattern": PACKAGE_NAME.pattern},
                },
            },
            "required": ["packages"],
            "additionalProperties": False,
        },
    },
    {
        "name": "desktop_open_url",
        "description": "Open an HTTP/HTTPS URL in the logged-in KDE desktop's default browser. Requires desktop-control enrolment and an available desktop session.",
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string", "minLength": 8, "maxLength": 2048}},
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "desktop_launch_app",
        "description": "Launch a supported installed desktop application in KDE. No arbitrary command or arguments are accepted.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "app": {"type": "string", "enum": ["konsole", "dolphin", "firedragon", "tangram", "kate", "libreoffice", "code", "systemsettings", "spectacle", "firefox", "okular", "thunderbird"]},
            },
            "required": ["app"],
            "additionalProperties": False,
        },
    },
    {
        "name": "desktop_screenshot",
        "description": "Capture the visible KDE desktop and return the screenshot to ChatGPT. This shares whatever is visible on screen; requires desktop-control enrolment.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "desktop_list_windows",
        "description": "List visible KDE window titles/classes using kdotool. Window titles may contain private information. Requires desktop-control enrolment.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "desktop_window_action",
        "description": "Activate, minimise or close a named KWin window ID returned by desktop_list_windows. Closing a window may discard unsaved work.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["activate", "minimize", "close"]},
                "window_id": {"type": "string", "pattern": WINDOW_ID.pattern},
            },
            "required": ["action", "window_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "desktop_type_text",
        "description": "Type supplied text into the currently focused KDE window using wtype. It does not decide which field has focus; verify the screenshot first for sensitive actions.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 4000}},
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "desktop_press_key",
        "description": "Press a keyboard key with optional ctrl/alt/shift/super modifiers in the currently focused KDE window.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "pattern": "^[A-Za-z0-9_]{1,24}$"},
                "modifiers": {"type": "array", "maxItems": 4, "items": {"type": "string", "enum": ["ctrl", "alt", "shift", "super"]}},
            },
            "required": ["key"],
            "additionalProperties": False,
        },
    },
    {
        "name": "desktop_click",
        "description": "Move the KDE pointer to pixel coordinates and click using ydotool. Requires ydotoold and its existing /dev/uinput permissions; it will not configure root access.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "minimum": 0, "maximum": 16384},
                "y": {"type": "integer", "minimum": 0, "maximum": 16384},
                "button": {"type": "string", "enum": ["left", "right", "middle"]},
            },
            "required": ["x", "y"],
            "additionalProperties": False,
        },
    },
]


def require_nonroot() -> None:
    if os.name != "posix" or os.geteuid() == 0:
        raise PermissionError("Direct PC tools must run as the ordinary Garuda user, never root.")


def _default_permissions() -> dict:
    return {"schema": SCHEMA, **{name: False for name in CAPABILITIES}}


def _assert_private_state(path: Path) -> None:
    if STATE_ROOT.is_symlink() or STATE_DIR.is_symlink() or path.is_symlink():
        raise PermissionError("Unsafe direct-MCP local state path.")
    if path.exists():
        st = path.stat()
        if st.st_uid != os.geteuid() or (st.st_mode & 0o077):
            raise PermissionError("Direct-MCP policy and audit files must be private and owned by the host user.")


def permissions_data(path: Path = PERMISSIONS) -> dict:
    if path == PERMISSIONS:
        _assert_private_state(path)
    elif path.is_symlink() or path.parent.is_symlink():
        raise PermissionError("Unsafe test/policy path.")
    if not path.exists():
        return _default_permissions()
    st = path.stat()
    if st.st_uid != os.geteuid() or (st.st_mode & 0o077):
        raise PermissionError("Direct-MCP consent must be private and owned by the host user.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PermissionError("Direct-MCP consent policy is unreadable.") from None
    if not isinstance(data, dict):
        raise PermissionError("Direct-MCP consent policy must be an object.")
    if data.get("schema") == LEGACY_SCHEMA:
        if set(data) != {"schema", "document_creation"} or type(data.get("document_creation")) is not bool:
            raise PermissionError("Invalid legacy direct-MCP consent policy.")
        upgraded = _default_permissions()
        upgraded["document_creation"] = data["document_creation"]
        return upgraded
    if data.get("schema") != SCHEMA or set(data) != {"schema", *CAPABILITIES}:
        raise PermissionError("Invalid direct-MCP consent policy schema.")
    if any(type(data.get(name)) is not bool for name in CAPABILITIES):
        raise PermissionError("Invalid direct-MCP capability value.")
    return data


def capability_status(path: Path = PERMISSIONS) -> dict:
    data = permissions_data(path)
    return {name: bool(data.get(name, False)) for name in CAPABILITIES}


def consent_status(path: Path = PERMISSIONS) -> bool:
    """Backward-compatible status helper for the document-creation capability."""
    return capability_status(path)["document_creation"]


def require_capability(name: str) -> None:
    if name not in CAPABILITIES:
        raise ValueError("Unknown direct-PC capability.")
    if not capability_status()[name]:
        hint = CAPABILITY_COMMANDS.get(name, "enable-capabilities --confirm")
        raise PermissionError(f"{name} is disabled. The PC owner must enable it locally with: python3 direct_pc_mcp.py {hint}")


def set_capabilities(changes: dict[str, bool], *, confirm: bool = False, all_enabled: bool | None = None) -> dict:
    require_nonroot()
    if confirm is not True:
        raise PermissionError("One-time local owner confirmation required.")
    if not isinstance(changes, dict) or any(name not in CAPABILITIES or type(value) is not bool for name, value in changes.items()):
        raise ValueError("Invalid direct-PC capability update.")
    current = permissions_data()
    updated = dict(current)
    if all_enabled is not None:
        updated = {"schema": SCHEMA, **{name: all_enabled for name in CAPABILITIES}}
    else:
        updated["schema"] = SCHEMA
        for name, value in changes.items():
            updated[name] = value
    STATE_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    if STATE_ROOT.is_symlink() or STATE_DIR.is_symlink() or PERMISSIONS.is_symlink():
        raise PermissionError("Unsafe direct-MCP policy path.")
    secure_write(PERMISSIONS, json.dumps(updated, sort_keys=True, indent=2) + "\n")
    return {"capabilities": capability_status(), "scope": "local owner policy"}


def set_consent(enabled: bool, confirm: bool = False) -> dict:
    return set_capabilities({"document_creation": enabled}, confirm=confirm)


def _audit(action: str, target: str, outcome: str, details: dict | None = None) -> None:
    if STATE_ROOT.is_symlink() or STATE_DIR.is_symlink() or AUDIT.is_symlink():
        raise RuntimeError("Unsafe direct-MCP audit path.")
    STATE_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    record = {
        "ts": int(time.time()),
        "actor": "direct-mcp-tool-call",
        "action": action,
        "target": target[:300],
        "outcome": outcome,
        "details": details or {},
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(AUDIT, flags, 0o600)
    try:
        st = os.fstat(fd)
        if st.st_uid != os.geteuid() or not stat.S_ISREG(st.st_mode) or (st.st_mode & 0o077):
            raise RuntimeError("Unsafe direct-MCP audit file.")
        with os.fdopen(fd, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def _http_health(port: int) -> dict:
    try:
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
            value = json.load(resp)
        if isinstance(value, dict):
            result = {k: value[k] for k in ("ok", "status", "worker", "active_or_queued", "max_concurrent_builds")
                      if k in value and isinstance(value[k], (str, int, bool, type(None)))}
            return {"reachable": True, **result}
    except (OSError, TimeoutError, ValueError):
        pass
    return {"reachable": False}


def _systemd_state(unit: str) -> str:
    try:
        result = subprocess.run(["/usr/bin/systemctl", "--user", "is-active", unit],
                                stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=3, check=False)
        state = result.stdout.strip()
        return state if state in ("active", "inactive", "failed", "activating", "deactivating") else "unverified"
    except (OSError, subprocess.TimeoutExpired):
        return "unverified"


def _safe_env() -> dict[str, str]:
    home = str(Path.home())
    path_parts = [
        str(Path(home) / ".local" / "bin"),
        str(Path(home) / ".cargo" / "bin"),
        str(Path(home) / ".local" / "share" / "pnpm"),
        str(Path(home) / ".npm" / "bin"),
        "/usr/local/bin", "/usr/bin", "/bin",
    ]
    env = {
        "HOME": home,
        "USER": os.environ.get("USER", Path(home).name),
        "LOGNAME": os.environ.get("LOGNAME", Path(home).name),
        "PATH": os.pathsep.join(path_parts),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": "C.UTF-8",
        "GIT_TERMINAL_PROMPT": "0",
        "CI": "1",
    }
    allowed = {
        "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "WAYLAND_DISPLAY",
        "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "DESKTOP_SESSION",
        "KDE_SESSION_VERSION", "XAUTHORITY", "QT_QPA_PLATFORM", "XDG_DATA_HOME",
        "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "TMPDIR",
    }
    for key in allowed:
        value = os.environ.get(key)
        if value:
            env[key] = value
    # A lingering systemd user manager may have the desktop variables even if
    # the tunnel daemon started before KDE login. Import only a tiny allowlist.
    systemctl = "/usr/bin/systemctl"
    if Path(systemctl).is_file():
        try:
            proc = subprocess.run([systemctl, "--user", "show-environment"],
                                  stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, timeout=2, check=False, env=env)
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    key, sep, value = line.partition("=")
                    if sep and key in allowed and value:
                        env[key] = value
        except (OSError, subprocess.TimeoutExpired):
            pass
    return env


def _desktop_session() -> dict:
    env = _safe_env()
    return {
        "available": bool(env.get("XDG_RUNTIME_DIR") and (env.get("WAYLAND_DISPLAY") or env.get("DISPLAY"))),
        "session_type": env.get("XDG_SESSION_TYPE", "unknown"),
        "wayland_display_present": bool(env.get("WAYLAND_DISPLAY")),
        "x11_display_present": bool(env.get("DISPLAY")),
        "dbus_session_present": bool(env.get("DBUS_SESSION_BUS_ADDRESS")),
        "tools": {
            name: bool(shutil.which(name, path=env.get("PATH")))
            for name in ("xdg-open", "spectacle", "grim", "kdotool", "wtype", "ydotool", "pkexec")
        },
    }


def _roots() -> list[dict]:
    home = Path.home()
    username = home.name
    candidates = [
        ("documents", home / "Documents"),
        ("downloads", home / "Downloads"),
        ("desktop", home / "Desktop"),
        ("projects", home / "Code_Projects"),
        ("projects", Path("/mnt/kingston/home") / username / "Code_Projects"),
    ]
    found: list[dict] = []
    seen: set[str] = set()
    for label, path in candidates:
        try:
            if path.is_symlink() or not path.is_dir():
                continue
            resolved = path.resolve(strict=True)
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            found.append({"label": label, "path": key})
        except OSError:
            continue
    return found


def _is_sensitive_name(name: str) -> bool:
    lower = name.lower()
    if lower in SENSITIVE_BASENAMES:
        return True
    if lower == ".env" or (lower.startswith(".env.") and lower not in SAFE_ENV_EXAMPLE_NAMES):
        return True
    if lower.startswith(("credentials", "secret-")) or lower in {"secrets", "secret"}:
        return True
    if lower.startswith(("id_rsa", "id_ed25519")):
        return True
    if Path(lower).suffix in {".pem", ".key", ".p12", ".pfx", ".kdbx", ".gpg", ".age"}:
        return True
    return False


def _is_sensitive_path(path: Path) -> bool:
    return any(part.lower() in BLOCKED_DIRS or _is_sensitive_name(part) for part in path.parts)


def _is_text_path(path: Path) -> bool:
    if path.name in TEXT_BASENAMES or path.name in SAFE_ENV_EXAMPLE_NAMES:
        return True
    return path.suffix.lower() in TEXT_EXTENSIONS or path.name.endswith((".env.example", ".env.sample", ".env.template"))


def _resolve_user_path(raw: str, *, projects_only: bool = False, must_exist: bool = False) -> tuple[Path, str, Path]:
    if not isinstance(raw, str) or len(raw) > 1024 or "\x00" in raw:
        raise ValueError("Invalid local path.")
    value = raw.strip()
    if not value:
        raise ValueError("A path is required.")
    expanded = Path(os.path.expanduser(value))
    if ".." in expanded.parts:
        raise PermissionError("Parent-directory traversal is not allowed.")
    roots = _roots()
    if not expanded.is_absolute():
        project = next((Path(x["path"]) for x in roots if x["label"] == "projects"), None)
        if project is None:
            raise RuntimeError("No approved Code_Projects directory is available on this PC.")
        expanded = project / expanded
    expanded = Path(os.path.abspath(str(expanded)))
    for root_info in roots:
        label = root_info["label"]
        root = Path(root_info["path"])
        if projects_only and label != "projects":
            continue
        try:
            relative = expanded.relative_to(root)
        except ValueError:
            continue
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise PermissionError("Symlink traversal is blocked for direct-PC file operations.")
        resolved = expanded.resolve(strict=False)
        if not resolved.is_relative_to(root):
            raise PermissionError("The requested path escapes its approved root.")
        if _is_sensitive_path(relative):
            raise PermissionError("Sensitive configuration, credential or cache paths are blocked.")
        if must_exist and not expanded.exists():
            raise FileNotFoundError("The requested local path does not exist.")
        return expanded, label, root
    raise PermissionError("Path is outside the approved Documents, Downloads, Desktop and Code_Projects roots.")


def _known_secret_values() -> list[str]:
    values: set[str] = set()
    for key, raw in os.environ.items():
        if re.search(r"(?:TOKEN|API[_-]?KEY|SECRET|PASSWORD|CREDENTIAL|PRIVATE[_-]?KEY)", key, re.I):
            value = raw.strip()
            if len(value) >= 8:
                values.add(value)
    # Redact the runtime credential if a command accidentally prints the local
    # key file. Read only this known file, and only when it is owner-private.
    known_paths = [Path.home() / ".config" / "open-ci-hub" / "tunnel-runtime-key"]
    for path in known_paths:
        try:
            if path.is_symlink() or not path.is_file():
                continue
            st = path.stat()
            if st.st_uid != os.geteuid() or (st.st_mode & 0o077) or st.st_size > 8192:
                continue
            value = path.read_text(encoding="utf-8").strip()
            if len(value) >= 8:
                values.add(value)
        except (OSError, UnicodeError):
            continue
    return sorted(values, key=len, reverse=True)


def _redact(text: str) -> tuple[str, int]:
    count = 0
    value = text
    for pattern, replacement in SECRET_PATTERNS:
        value, hits = pattern.subn(replacement, value)
        count += hits
    for secret in _known_secret_values():
        hits = value.count(secret)
        if hits:
            value = value.replace(secret, "[REDACTED LOCAL SECRET]")
            count += hits
    return value, count


def _read_text_file(path: Path) -> tuple[str, str, int]:
    if path.is_symlink() or not path.is_file():
        raise PermissionError("Only regular, non-symlink files can be read.")
    if _is_sensitive_name(path.name) or not _is_text_path(path):
        raise PermissionError("This file type or name is not allowed for direct text access.")
    st = path.stat()
    if st.st_size > MAX_FILE_BYTES:
        raise ValueError(f"File exceeds the {MAX_FILE_BYTES}-byte direct-read limit.")
    raw = path.read_bytes()
    if b"\x00" in raw:
        raise ValueError("Binary files cannot be returned through the text-file tool.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("File is not valid UTF-8 text.") from None
    digest = hashlib.sha256(raw).hexdigest()
    redacted, redaction_count = _redact(text)
    return redacted, digest, redaction_count


def pc_status() -> dict:
    require_nonroot()
    disk = shutil.disk_usage(Path.home())
    memory = {}
    try:
        for line in Path("/proc/meminfo").read_text("ascii").splitlines():
            name, _, amount = line.partition(":")
            if name in ("MemTotal", "MemAvailable", "SwapFree"):
                memory[name.lower() + "_mb"] = int(amount.strip().split()[0]) // 1024
    except (OSError, ValueError, IndexError):
        memory = {"status": "unverified"}
    caps = capability_status()
    return {
        "host_kind": "live_owner_garuda_linux",
        "disk_free_gb": round(disk.free / (1024 ** 3), 2),
        "load_average_1m": round(os.getloadavg()[0], 2),
        "memory": memory,
        "services": {
            "dashboard": _systemd_state("open-ci-hub-dashboard.service"),
            "host_agent": _systemd_state("open-ci-hub-host-agent.service"),
            "legacy_ci": _systemd_state("custom-ci-api.service"),
        },
        "dashboard_health": _http_health(8765),
        "legacy_ci_health": _http_health(8090),
        "document_creation_enabled": caps["document_creation"],
        "capabilities": caps,
        "approved_roots": _roots(),
        "desktop_session": _desktop_session(),
        "provenance_note": "Live local data. Secure MCP Tunnel authenticates the control-plane connection, but stdio MCP does not sign the original ChatGPT message or supply a verified end-user identity.",
    }


def create_document(filename: str, content: str) -> dict:
    require_nonroot()
    require_capability("document_creation")
    if not isinstance(filename, str) or not DOC_NAME.fullmatch(filename):
        raise ValueError("Only a safe new .md or .txt basename is allowed.")
    if not isinstance(content, str) or not 1 <= len(content.encode("utf-8")) <= MAX_DOC_BYTES or "\x00" in content:
        raise ValueError("UTF-8 document must contain 1–4000 bytes, without NUL.")
    output_dir = Path.home() / "Documents" / "OpenCIHubTasks"
    if output_dir.parent.is_symlink() or output_dir.is_symlink():
        raise PermissionError("Symlinked document directory is forbidden.")
    output_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    st = output_dir.stat()
    if st.st_uid != os.geteuid() or (st.st_mode & 0o077):
        raise PermissionError("Document destination must be private and owner-owned.")
    _audit("create_pc_document", filename, "requested", {"bytes": len(content.encode("utf-8"))})
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    target = output_dir / filename
    payload = content.encode("utf-8")
    fd = os.open(target, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    digest = hashlib.sha256(payload).hexdigest()
    _audit("create_pc_document", filename, "success", {"bytes": len(payload), "sha256": digest})
    return {"result": "created", "filename": filename, "folder": "~/Documents/OpenCIHubTasks",
            "bytes": len(payload), "sha256": digest}


def list_pc_directory(path: str = "") -> dict:
    require_nonroot()
    require_capability("file_browse")
    if not path:
        roots = _roots()
        _audit("list_pc_directory", "approved roots", "success", {"count": len(roots)})
        return {"roots": roots, "note": "Relative paths resolve from the first approved Code_Projects root."}
    directory, label, _ = _resolve_user_path(path, must_exist=True)
    if not directory.is_dir():
        raise ValueError("Requested path is not a directory.")
    entries = []
    try:
        children = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        raise PermissionError("Directory cannot be listed.") from None
    for child in children:
        if child.name.lower() in BLOCKED_DIRS or _is_sensitive_name(child.name):
            continue
        try:
            if child.is_symlink():
                continue
            st = child.stat()
            kind = "directory" if stat.S_ISDIR(st.st_mode) else "file" if stat.S_ISREG(st.st_mode) else "other"
            if kind == "other":
                continue
            entries.append({
                "name": child.name[:255],
                "type": kind,
                "size_bytes": st.st_size if kind == "file" else None,
                "modified_epoch": int(st.st_mtime),
            })
        except OSError:
            continue
        if len(entries) >= MAX_DIRECTORY_ENTRIES:
            break
    _audit("list_pc_directory", str(directory), "success", {"entries": len(entries)})
    return {"path": str(directory), "root_type": label, "entries": entries,
            "truncated": len(entries) >= MAX_DIRECTORY_ENTRIES}


def read_pc_file(path: str) -> dict:
    require_nonroot()
    require_capability("file_read")
    target, label, _ = _resolve_user_path(path, must_exist=True)
    text, digest, redaction_count = _read_text_file(target)
    _audit("read_pc_file", str(target), "success", {"sha256": digest, "redactions": redaction_count})
    return {"path": str(target), "root_type": label, "content": text,
            "sha256": digest, "redactions_applied": redaction_count,
            "note": "Content is redacted for common credential patterns; this is not a guarantee that every secret format is detected."}


def _private_backup(path: Path) -> str:
    if STATE_ROOT.is_symlink() or STATE_DIR.is_symlink():
        raise PermissionError("Unsafe direct-MCP backup path.")
    STATE_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    backup_dir = STATE_DIR / "backups"
    backup_dir.mkdir(mode=0o700, exist_ok=True)
    if backup_dir.is_symlink():
        raise PermissionError("Symlinked backup directory is forbidden.")
    os.chmod(backup_dir, 0o700)
    stamp = str(time.time_ns())
    digest = hashlib.sha256(str(path).encode()).hexdigest()[:16]
    backup = backup_dir / f"{digest}-{stamp}.bak"
    data = path.read_bytes()
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(backup, flags, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return str(backup)


def write_pc_file(path: str, content: str, expected_sha256: str | None) -> dict:
    require_nonroot()
    require_capability("file_edit")
    target, _, root = _resolve_user_path(path, projects_only=True)
    if not _is_text_path(target) or _is_sensitive_name(target.name):
        raise PermissionError("Only non-sensitive text/source files may be created or edited.")
    if not isinstance(content, str) or "\x00" in content:
        raise ValueError("File content must be text without NUL bytes.")
    payload = content.encode("utf-8")
    if len(payload) > MAX_FILE_BYTES:
        raise ValueError(f"File content exceeds {MAX_FILE_BYTES} bytes.")
    if not target.parent.is_dir() or target.parent.is_symlink():
        raise ValueError("Parent directory must already exist and cannot be a symlink.")
    backup = None
    existed = target.exists()
    if existed:
        if target.is_symlink() or not target.is_file():
            raise PermissionError("Only regular, non-symlink files can be replaced.")
        st = target.stat()
        if st.st_uid != os.geteuid():
            raise PermissionError("Refusing to edit a file not owned by the desktop user.")
        if st.st_size > MAX_FILE_BYTES:
            raise ValueError(f"Existing file exceeds the {MAX_FILE_BYTES}-byte edit limit.")
        if expected_sha256 is None:
            raise ValueError("Existing files require their current expected_sha256.")
        raw_current = target.read_bytes()
        current = hashlib.sha256(raw_current).hexdigest()
        if current != expected_sha256:
            raise RuntimeError("File changed since it was read; re-read it before editing.")
        try:
            original_text = raw_current.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("Existing file is not valid UTF-8; refusing to edit it through the redaction-aware text tool.") from None
        _, redaction_count = _redact(original_text)
        if redaction_count:
            _audit("write_pc_file", str(target), "blocked_redacted_content",
                   {"redactions": redaction_count})
            raise PermissionError(
                "This file contains credential-like content that is redacted on read. "
                "Direct editing is blocked to prevent replacing hidden values with redaction "
                "placeholders or silently deleting them. Edit it locally or use a future "
                "secret-preserving edit operation."
            )
        backup = _private_backup(target)
        mode = stat.S_IMODE(st.st_mode)
    else:
        if expected_sha256 is not None:
            raise ValueError("New files require expected_sha256=null.")
        mode = 0o644
    _audit("write_pc_file", str(target), "requested",
           {"content_sha256": hashlib.sha256(payload).hexdigest(), "backup_created": bool(backup)})
    fd, temp_name = tempfile.mkstemp(prefix=".open-ci-edit-", dir=str(target.parent))
    temp_path = Path(temp_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if existed:
            if not target.exists() or target.is_symlink():
                raise RuntimeError("File was removed or replaced during edit; no write performed.")
            current_stat = target.stat()
            if current_stat.st_size > MAX_FILE_BYTES or hashlib.sha256(target.read_bytes()).hexdigest() != expected_sha256:
                raise RuntimeError("File changed during edit; no write performed.")
            os.replace(temp_path, target)
        else:
            # Link-based create is exclusive: never replace a file created concurrently.
            if target.exists() or target.is_symlink():
                raise RuntimeError("A file was created at this path concurrently; no overwrite performed.")
            os.link(temp_path, target, follow_symlinks=False)
            temp_path.unlink(missing_ok=True)
        try:
            parent_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        except OSError:
            pass
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    new_sha = hashlib.sha256(payload).hexdigest()
    _audit("write_pc_file", str(target), "success", {"sha256": new_sha, "backup": backup})
    return {"result": "written", "path": str(target), "sha256": new_sha,
            "bytes": len(payload), "backup": backup, "root": str(root)}


def _run_bounded(argv: list[str], *, cwd: str | None = None, env: dict | None = None,
                 timeout: int = 30, stdin=None) -> dict:
    process = subprocess.Popen(
        argv, cwd=cwd, env=env or _safe_env(), stdin=stdin if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=False, close_fds=True,
        start_new_session=True,
    )
    collected = bytearray()
    total = [0]

    def drain() -> None:
        assert process.stdout is not None
        while True:
            chunk = process.stdout.read(4096)
            if not chunk:
                break
            total[0] += len(chunk)
            remaining = MAX_OUTPUT_BYTES - len(collected)
            if remaining > 0:
                collected.extend(chunk[:remaining])

    reader = threading.Thread(target=drain, name="direct-pc-output", daemon=True)
    reader.start()
    timed_out = False
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            process.wait(timeout=2)
        code = process.returncode if process.returncode is not None else 124
    reader.join(timeout=5)
    if reader.is_alive():
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
        reader.join(timeout=2)
    output = collected.decode("utf-8", "replace")
    redacted, redactions = _redact(output)
    return {
        "exit_code": int(code if code is not None else 1),
        "timed_out": timed_out,
        "output": redacted,
        "output_bytes_seen": total[0],
        "output_truncated": total[0] > len(collected),
        "redactions_applied": redactions,
    }


def run_pc_command(command: str, cwd: str = "", timeout_seconds: int = 120) -> dict:
    require_nonroot()
    require_capability("terminal_execution")
    if not isinstance(command, str) or not command.strip() or len(command) > MAX_COMMAND_CHARS or "\x00" in command:
        raise ValueError("Command must be 1–8000 characters of text.")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= MAX_COMMAND_SECONDS:
        raise ValueError(f"timeout_seconds must be 1–{MAX_COMMAND_SECONDS}.")
    requested_cwd = cwd or "."
    workdir, _, _ = _resolve_user_path(requested_cwd, projects_only=True, must_exist=True)
    if not workdir.is_dir():
        raise ValueError("Command working directory must be an approved project directory.")
    env = _safe_env()
    fish = shutil.which("fish", path=env["PATH"])
    if fish:
        argv = [fish, "--no-config", "-c", command]
    else:
        bash = shutil.which("bash", path=env["PATH"])
        if not bash:
            raise RuntimeError("Neither fish nor bash is available on the host.")
        argv = [bash, "--noprofile", "--norc", "-c", command]
    command_hash = hashlib.sha256(command.encode("utf-8")).hexdigest()
    _audit("run_pc_command", str(workdir), "requested",
           {"command_sha256": command_hash, "timeout_seconds": timeout_seconds})
    started = time.monotonic()
    try:
        result = _run_bounded(argv, cwd=str(workdir), env=env, timeout=timeout_seconds)
    except OSError as exc:
        _audit("run_pc_command", str(workdir), "failed", {"command_sha256": command_hash, "error": type(exc).__name__})
        raise RuntimeError(f"Could not start local command: {exc.__class__.__name__}.") from None
    duration = round(time.monotonic() - started, 3)
    _audit("run_pc_command", str(workdir), "success" if result["exit_code"] == 0 and not result["timed_out"] else "failed",
           {"command_sha256": command_hash, "exit_code": result["exit_code"], "timed_out": result["timed_out"], "duration_seconds": duration})
    return {
        "cwd": str(workdir),
        "command_sha256": command_hash,
        "duration_seconds": duration,
        **result,
        "note": "Command ran as the ordinary desktop user, not root. Output is bounded and redacted for common credential patterns.",
    }


def install_arch_packages(packages: list[str]) -> dict:
    require_nonroot()
    require_capability("software_install")
    if not isinstance(packages, list) or not 1 <= len(packages) <= 10:
        raise ValueError("Supply between 1 and 10 official-repository package names.")
    if any(not isinstance(p, str) or not PACKAGE_NAME.fullmatch(p) or p.startswith("-") for p in packages):
        raise ValueError("Package names may contain only letters, digits, @, dot, underscore, plus and hyphen.")
    if not Path("/usr/bin/pacman").is_file():
        raise RuntimeError("This installer supports only Arch/Garuda pacman systems.")
    env = _safe_env()
    pkexec = shutil.which("pkexec", path=env["PATH"])
    if not pkexec:
        raise RuntimeError("pkexec is unavailable; no privilege escalation was attempted.")
    if not _desktop_session().get("available"):
        raise RuntimeError("No live KDE display session is available for the normal polkit authentication prompt.")
    argv = [pkexec, "/usr/bin/pacman", "-S", "--needed", "--noconfirm", *packages]
    _audit("install_arch_packages", ",".join(packages), "requested", {"manager": "pacman"})
    try:
        result = _run_bounded(argv, env=env, timeout=MAX_INSTALL_SECONDS)
    except OSError as exc:
        _audit("install_arch_packages", ",".join(packages), "failed", {"error": type(exc).__name__})
        raise RuntimeError(f"Could not start the package installer: {exc.__class__.__name__}.") from None
    _audit("install_arch_packages", ",".join(packages), "success" if result["exit_code"] == 0 and not result["timed_out"] else "failed",
           {"exit_code": result["exit_code"], "timed_out": result["timed_out"]})
    return {"packages": packages, **result,
            "note": "Official pacman repositories only. Any polkit authentication prompt remains controlled by the desktop owner; AUR builds are not run by this tool."}


def _require_desktop() -> dict:
    require_nonroot()
    require_capability("desktop_control")
    env = _safe_env()
    if not env.get("XDG_RUNTIME_DIR") or not (env.get("WAYLAND_DISPLAY") or env.get("DISPLAY")):
        raise RuntimeError("The background tunnel process cannot see an active KDE display session. Import the KDE session environment into the user service, then restart the tunnel runtime.")
    return env


def desktop_open_url(url: str) -> dict:
    env = _require_desktop()
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError("Invalid URL.")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only HTTP/HTTPS URLs without embedded credentials can be opened.")
    opener = shutil.which("xdg-open", path=env["PATH"])
    if not opener:
        raise RuntimeError("xdg-open is not installed.")
    _audit("desktop_open_url", parsed.hostname, "requested", {"scheme": parsed.scheme})
    try:
        proc = subprocess.Popen([opener, url], env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                shell=False, close_fds=True, start_new_session=True)
    except OSError as exc:
        _audit("desktop_open_url", parsed.hostname, "failed", {"error": type(exc).__name__})
        raise RuntimeError(f"Could not open the URL: {exc.__class__.__name__}.") from None
    _audit("desktop_open_url", parsed.hostname, "launched", {"pid": proc.pid})
    return {"launched": True, "pid": proc.pid, "host": parsed.hostname, "scheme": parsed.scheme}


APP_CANDIDATES = {
    "konsole": ("konsole",),
    "dolphin": ("dolphin",),
    "firedragon": ("firedragon", "firedragon-bin"),
    "tangram": ("tangram",),
    "kate": ("kate",),
    "libreoffice": ("libreoffice",),
    "code": ("code",),
    "systemsettings": ("systemsettings", "systemsettings6"),
    "spectacle": ("spectacle",),
    "firefox": ("firefox",),
    "okular": ("okular",),
    "thunderbird": ("thunderbird",),
}


def desktop_launch_app(app: str) -> dict:
    env = _require_desktop()
    if app not in APP_CANDIDATES:
        raise ValueError("Unsupported desktop application.")
    executable = next((shutil.which(x, path=env["PATH"]) for x in APP_CANDIDATES[app]
                       if shutil.which(x, path=env["PATH"])), None)
    if not executable:
        raise RuntimeError(f"The requested app is not installed or not on PATH: {app}.")
    _audit("desktop_launch_app", app, "requested")
    try:
        proc = subprocess.Popen([executable], env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                shell=False, close_fds=True, start_new_session=True)
    except OSError as exc:
        _audit("desktop_launch_app", app, "failed", {"error": type(exc).__name__})
        raise RuntimeError(f"Could not launch {app}: {exc.__class__.__name__}.") from None
    _audit("desktop_launch_app", app, "launched", {"pid": proc.pid})
    return {"launched": True, "app": app, "pid": proc.pid}


def desktop_screenshot() -> dict:
    env = _require_desktop()
    with tempfile.TemporaryDirectory(prefix="open-ci-desktop-") as directory:
        output_path = Path(directory) / "screen.png"
        spectacle = shutil.which("spectacle", path=env["PATH"])
        grim = shutil.which("grim", path=env["PATH"])
        if spectacle:
            argv = [spectacle, "-b", "-n", "-f", "-o", str(output_path)]
        elif grim:
            argv = [grim, str(output_path)]
        else:
            raise RuntimeError("Neither KDE Spectacle nor grim is installed for screenshots.")
        result = _run_bounded(argv, env=env, timeout=30)
        if result["exit_code"] != 0 or result["timed_out"] or not output_path.is_file():
            raise RuntimeError("Desktop screenshot failed. Check the KDE portal/display environment and screenshot utility.")
        data = output_path.read_bytes()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("Screenshot utility did not produce a PNG image.")
        if len(data) > MAX_SCREENSHOT_BYTES:
            raise RuntimeError("Screenshot exceeds the 8 MiB response limit.")
        digest = hashlib.sha256(data).hexdigest()
        _audit("desktop_screenshot", "visible desktop", "success", {"bytes": len(data), "sha256": digest})
        return {
            "_mcp_content_blocks": [
                {"type": "text", "text": json.dumps({"captured": True, "bytes": len(data), "sha256": digest,
                                                       "note": "The visible screen was shared with ChatGPT."})},
                {"type": "image", "data": base64.b64encode(data).decode("ascii"), "mimeType": "image/png"},
            ]
        }


def desktop_list_windows() -> dict:
    env = _require_desktop()
    kdotool = shutil.which("kdotool", path=env["PATH"])
    if not kdotool:
        raise RuntimeError("kdotool is not installed. Install it from the official Arch/Garuda repository if available.")
    result = _run_bounded([kdotool, "search", "--name", ".*"], env=env, timeout=15)
    if result["exit_code"] != 0:
        raise RuntimeError("kdotool could not query KWin windows: " + result["output"][:300])
    windows = []
    ids = [line.strip() for line in result["output"].splitlines() if WINDOW_ID.fullmatch(line.strip())]
    for window_id in ids[:40]:
        name = _run_bounded([kdotool, "getwindowname", window_id], env=env, timeout=3)
        klass = _run_bounded([kdotool, "getwindowclassname", window_id], env=env, timeout=3)
        windows.append({
            "window_id": window_id,
            "title": _redact(name["output"].strip())[0][:240],
            "class": klass["output"].strip()[:120],
        })
    _audit("desktop_list_windows", "KWin", "success", {"windows": len(windows)})
    return {"windows": windows, "truncated": len(ids) > 40}


def desktop_window_action(action: str, window_id: str) -> dict:
    env = _require_desktop()
    if action not in ("activate", "minimize", "close") or not isinstance(window_id, str) or not WINDOW_ID.fullmatch(window_id):
        raise ValueError("Invalid KWin action or window ID.")
    kdotool = shutil.which("kdotool", path=env["PATH"])
    if not kdotool:
        raise RuntimeError("kdotool is not installed.")
    command = {"activate": "windowactivate", "minimize": "windowminimize", "close": "windowclose"}[action]
    _audit("desktop_window_action", window_id, "requested", {"action": action})
    result = _run_bounded([kdotool, command, window_id], env=env, timeout=10)
    if result["exit_code"] != 0:
        _audit("desktop_window_action", window_id, "failed", {"action": action, "exit_code": result["exit_code"]})
        raise RuntimeError("KWin rejected the window action: " + result["output"][:300])
    _audit("desktop_window_action", window_id, "success", {"action": action})
    return {"success": True, "action": action, "window_id": window_id}


def desktop_type_text(text: str) -> dict:
    env = _require_desktop()
    if not isinstance(text, str) or not 1 <= len(text) <= 4000 or "\x00" in text:
        raise ValueError("Text must contain 1–4000 characters without NUL.")
    wtype = shutil.which("wtype", path=env["PATH"])
    if not wtype:
        raise RuntimeError("wtype is not installed. It is the Wayland keyboard text-input backend.")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    _audit("desktop_type_text", "focused window", "requested", {"text_sha256": digest, "characters": len(text)})
    try:
        proc = subprocess.run([wtype, "-"], input=text, capture_output=True, text=True,
                              env=env, timeout=15, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError("wtype timed out.") from None
    output, _ = _redact((proc.stdout or "") + (proc.stderr or ""))
    if proc.returncode != 0:
        _audit("desktop_type_text", "focused window", "failed", {"text_sha256": digest, "exit_code": proc.returncode})
        raise RuntimeError("wtype failed: " + output[:250])
    _audit("desktop_type_text", "focused window", "success", {"text_sha256": digest, "characters": len(text)})
    return {"typed": True, "characters": len(text), "text_sha256": digest}


def desktop_press_key(key: str, modifiers: list[str] | None = None) -> dict:
    env = _require_desktop()
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,24}", key):
        raise ValueError("Invalid key name.")
    modifiers = modifiers or []
    allowed_modifiers = {"ctrl", "alt", "shift", "super"}
    if not isinstance(modifiers, list) or len(modifiers) > 4 or any(x not in allowed_modifiers for x in modifiers) or len(set(modifiers)) != len(modifiers):
        raise ValueError("Modifiers must be distinct members of ctrl, alt, shift and super.")
    wtype = shutil.which("wtype", path=env["PATH"])
    if not wtype:
        raise RuntimeError("wtype is not installed. It is the Wayland keyboard input backend.")
    argv = [wtype]
    for modifier in modifiers:
        argv.extend(["-M", modifier])
    argv.extend(["-P", key, "-p", key])
    for modifier in reversed(modifiers):
        argv.extend(["-m", modifier])
    result = _run_bounded(argv, env=env, timeout=10)
    if result["exit_code"] != 0:
        raise RuntimeError("wtype keypress failed: " + result["output"][:250])
    _audit("desktop_press_key", "focused window", "success", {"key": key, "modifiers": modifiers})
    return {"pressed": True, "key": key, "modifiers": modifiers}


def desktop_click(x: int, y: int, button: str = "left") -> dict:
    env = _require_desktop()
    if type(x) is not int or type(y) is not int or not (0 <= x <= 16384 and 0 <= y <= 16384):
        raise ValueError("Pointer coordinates must be integers between 0 and 16384.")
    if button not in ("left", "right", "middle"):
        raise ValueError("Button must be left, right or middle.")
    ydotool = shutil.which("ydotool", path=env["PATH"])
    if not ydotool:
        raise RuntimeError("ydotool is not installed.")
    moved = _run_bounded([ydotool, "mousemove", "--absolute", str(x), str(y)], env=env, timeout=5)
    if moved["exit_code"] != 0:
        raise RuntimeError("ydotool could not move the pointer; check ydotoold and /dev/uinput permissions.")
    button_code = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}[button]
    clicked = _run_bounded([ydotool, "click", button_code], env=env, timeout=5)
    if clicked["exit_code"] != 0:
        raise RuntimeError("ydotool could not click; check ydotoold and /dev/uinput permissions.")
    _audit("desktop_click", f"{x},{y}", "success", {"button": button})
    return {"clicked": True, "x": x, "y": y, "button": button}


def invoke_tool(name: str, arguments: dict) -> dict:
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be a JSON object.")
    if name == "get_pc_status":
        if arguments:
            raise ValueError("This tool accepts no arguments.")
        return pc_status()
    if name == "create_pc_document":
        if set(arguments) != {"filename", "content"}:
            raise ValueError("Expected filename and content only.")
        return create_document(arguments["filename"], arguments["content"])
    if name == "list_pc_directory":
        if set(arguments) - {"path"}:
            raise ValueError("Expected optional path only.")
        return list_pc_directory(arguments.get("path", ""))
    if name == "read_pc_file":
        if set(arguments) != {"path"}:
            raise ValueError("Expected path only.")
        return read_pc_file(arguments["path"])
    if name == "write_pc_file":
        if set(arguments) != {"path", "content", "expected_sha256"}:
            raise ValueError("Expected path, content and expected_sha256 only.")
        return write_pc_file(arguments["path"], arguments["content"], arguments["expected_sha256"])
    if name == "run_pc_command":
        if set(arguments) - {"command", "cwd", "timeout_seconds"} or "command" not in arguments:
            raise ValueError("Expected command and optional cwd/timeout_seconds only.")
        return run_pc_command(arguments["command"], arguments.get("cwd", ""), arguments.get("timeout_seconds", 120))
    if name == "install_arch_packages":
        if set(arguments) != {"packages"}:
            raise ValueError("Expected packages only.")
        return install_arch_packages(arguments["packages"])
    if name == "desktop_open_url":
        if set(arguments) != {"url"}:
            raise ValueError("Expected URL only.")
        return desktop_open_url(arguments["url"])
    if name == "desktop_launch_app":
        if set(arguments) != {"app"}:
            raise ValueError("Expected app only.")
        return desktop_launch_app(arguments["app"])
    if name == "desktop_screenshot":
        if arguments:
            raise ValueError("This tool accepts no arguments.")
        return desktop_screenshot()
    if name == "desktop_list_windows":
        if arguments:
            raise ValueError("This tool accepts no arguments.")
        return desktop_list_windows()
    if name == "desktop_window_action":
        if set(arguments) != {"action", "window_id"}:
            raise ValueError("Expected action and window_id only.")
        return desktop_window_action(arguments["action"], arguments["window_id"])
    if name == "desktop_type_text":
        if set(arguments) != {"text"}:
            raise ValueError("Expected text only.")
        return desktop_type_text(arguments["text"])
    if name == "desktop_press_key":
        if set(arguments) - {"key", "modifiers"} or "key" not in arguments:
            raise ValueError("Expected key and optional modifiers only.")
        return desktop_press_key(arguments["key"], arguments.get("modifiers", []))
    if name == "desktop_click":
        if set(arguments) - {"x", "y", "button"} or not {"x", "y"}.issubset(arguments):
            raise ValueError("Expected x, y and optional button only.")
        return desktop_click(arguments["x"], arguments["y"], arguments.get("button", "left"))
    if name == "inspect_ci_failure":
        if set(arguments) != {"provider", "repository", "run_id"}:
            raise ValueError("Expected provider, repository and run_id only.")
        from operations import safe_repo, safe_run_id
        provider, repo = arguments["provider"], safe_repo(arguments["repository"])
        run = safe_run_id(arguments["run_id"])
        if provider not in ("github", "gitlab"):
            raise ValueError("Provider must be github or gitlab.")
        try:
            from ci_failure_diagnostics import diagnose
        except ImportError:
            raise RuntimeError("Failure inspection requires the Open CI Hub CI diagnostics module.") from None
        return diagnose(provider, repo, run, include_logs=True)
    raise ValueError("Unknown MCP tool.")


def mcp_response(message: dict) -> dict | None:
    method = message.get("method")
    rid = message.get("id")
    if not isinstance(method, str):
        raise ValueError("MCP method must be a string.")
    if "id" not in message:
        return None
    if rid is None or type(rid) not in (str, int):
        raise ValueError("Invalid MCP request ID.")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "open-ci-hub-direct-pc", "version": "0.2.0"},
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params", {})
        if not isinstance(params, dict):
            raise ValueError("Invalid tools/call params.")
        try:
            result = invoke_tool(params.get("name"), params.get("arguments", {}))
            if isinstance(result, dict) and isinstance(result.get("_mcp_content_blocks"), list):
                return {"jsonrpc": "2.0", "id": rid, "result": {
                    "content": result["_mcp_content_blocks"], "isError": False,
                }}
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": json.dumps(result, sort_keys=True, ensure_ascii=False)}],
                "isError": False,
            }}
        except (ValueError, PermissionError, RuntimeError, OSError, TypeError, KeyError) as exc:
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": str(exc)[:500]}],
                "isError": True,
            }}
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found."}}


def serve(in_stream=None, out_stream=None) -> None:
    require_nonroot()
    input_stream = in_stream if in_stream is not None else sys.stdin
    output_stream = out_stream if out_stream is not None else sys.stdout
    for line in input_stream:
        if len(line.encode("utf-8")) > MAX_INPUT_BYTES:
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
                continue
            response = mcp_response(request)
            if response is not None:
                output_stream.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
                output_stream.flush()
        except (ValueError, UnicodeError, TypeError):
            continue


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Local, explicitly enrolled ChatGPT tools for the Garuda PC")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    sub.add_parser("status")
    command_to_changes = {
        "enable-documents": {"document_creation": True},
        "disable-documents": {"document_creation": False},
        "enable-file-access": {"file_browse": True, "file_read": True, "file_edit": True},
        "disable-file-access": {"file_browse": False, "file_read": False, "file_edit": False},
        "enable-terminal": {"terminal_execution": True},
        "disable-terminal": {"terminal_execution": False},
        "enable-software-install": {"software_install": True},
        "disable-software-install": {"software_install": False},
        "enable-desktop-control": {"desktop_control": True},
        "disable-desktop-control": {"desktop_control": False},
    }
    for command in (*command_to_changes, "enable-capabilities", "disable-capabilities"):
        item = sub.add_parser(command)
        item.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    try:
        require_nonroot()
        if args.command == "serve":
            serve()
            return 0
        if args.command == "status":
            print(json.dumps(pc_status(), indent=2))
            return 0
        if args.command == "enable-capabilities":
            result = set_capabilities({}, confirm=args.confirm, all_enabled=True)
        elif args.command == "disable-capabilities":
            result = set_capabilities({}, confirm=args.confirm, all_enabled=False)
        else:
            result = set_capabilities(command_to_changes[args.command], confirm=args.confirm)
        print(json.dumps(result, indent=2))
        return 0
    except (RuntimeError, ValueError, PermissionError, OSError) as err:
        print("Open CI direct MCP error: " + str(err)[:350], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
