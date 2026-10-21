#!/usr/bin/env bash
# Install and start the optional Hub-owned CI execution bridge as a user service.
# Run from any directory; never run as root.
set -euo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Run as the ordinary desktop user; never sudo this installer." >&2
  exit 1
fi

root="$(cd "$(dirname "$0")/.." && pwd -P)"
python_bin="$(command -v python3)"
units="$HOME/.config/systemd/user"
state="$root/.local"

if [[ "$root:$python_bin" == *$'\n'* || "$root:$python_bin" == *$'\r'* || "$root:$python_bin" == *'%'* || "$root:$python_bin" == *'\'* ]]; then
  echo "Unsupported systemd unit path character." >&2
  exit 1
fi
if ! command -v systemctl >/dev/null 2>&1 || ! systemctl --user show-environment >/dev/null 2>&1; then
  echo "A working systemd user session is required." >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  echo "Docker Engine must be available to this user before enabling the bridge." >&2
  exit 1
fi

# Validate both files, schema and restrictive permissions without printing secrets.
python3 - "$root" <<'PY'
import sys
from pathlib import Path
root=Path(sys.argv[1]).resolve()
sys.path.insert(0,str(root))
import ci_execution_bridge as bridge
bridge.read_config()
bridge.read_secrets()
PY

mkdir -p "$units"
umask 077
unit="$units/open-ci-hub-execution-bridge.service"
temp="$(mktemp "$units/open-ci-hub-execution-bridge.XXXXXX.service")"
trap 'rm -f "$temp"' EXIT
cat > "$temp" <<UNIT
[Unit]
Description=Open CI Hub local-first execution bridge
After=network-online.target

[Service]
Type=simple
WorkingDirectory="$root"
ExecStart="$python_bin" "$root/ci_execution_bridge.py" serve --host 127.0.0.1 --port 8766
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=20
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectKernelTunables=true
ProtectControlGroups=true
ProtectKernelModules=true
LockPersonality=true
UMask=0077

[Install]
WantedBy=default.target
UNIT

if command -v systemd-analyze >/dev/null 2>&1; then
  systemd-analyze verify "$temp" || {
    echo "Refusing to install invalid unit; existing service file left intact." >&2
    exit 1
  }
fi

mv -f -- "$temp" "$unit"
trap - EXIT
systemctl --user daemon-reload
systemctl --user enable --now open-ci-hub-execution-bridge.service
systemctl --user --no-pager --full status open-ci-hub-execution-bridge.service
