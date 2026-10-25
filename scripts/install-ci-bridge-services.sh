#!/usr/bin/env bash
# Install non-root Open CI Hub outbound change discovery and resource-aware worker.
# Never modifies existing native runners, legacy CI, webhook tunnels or Docker volumes.
set -euo pipefail
if [[ "$(id -u)" -eq 0 ]]; then
    echo 'Run as your normal desktop user, without sudo.' >&2
    exit 1
fi
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
PYTHON="$(command -v python3)"
MANIFEST="$ROOT/.local/ci-bridge/manifest.json"
UNITS="$HOME/.config/systemd/user"
if [[ ! -f "$MANIFEST" ]]; then
    echo "Private allowlist missing: $MANIFEST" >&2
    echo 'Configure an owner-reviewed manifest with mode 0600 before installing.' >&2
    exit 1
fi
if ! command -v systemctl >/dev/null || ! systemctl --user show-environment >/dev/null 2>&1; then
    echo 'Working systemd user session required.' >&2
    exit 1
fi
python3 -m py_compile "$ROOT/ci_bridge.py" "$ROOT/ci_bridge_poll.py"
python3 - "$ROOT" <<'PY'
import sys
from pathlib import Path
root=Path(sys.argv[1])
sys.path.insert(0,str(root))
import ci_bridge
manifest=ci_bridge.load_manifest(root/'.local/ci-bridge/manifest.json')
if not manifest['repositories']:
    raise SystemExit('Refusing to enable idle services with no approved repositories.')
PY
mkdir -p "$UNITS"
umask 077
# Distinct user services. One lightweight watcher + one constrained worker pool.
# This cannot enlarge the existing legacy worker's concurrency, by design.
cat > "$UNITS/open-ci-hub-bridge-poll.service" <<UNIT
[Unit]
Description=Open CI Hub outbound GitHub/GitLab change discovery
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=$PYTHON $ROOT/ci_bridge_poll.py poll --interval 180
Environment=PYTHONUNBUFFERED=1
Environment=OPEN_CI_BRIDGE_MANIFEST=$MANIFEST
Restart=on-failure
RestartSec=15
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
MemoryMax=256M
CPUQuota=40%
UMask=0077

[Install]
WantedBy=default.target
UNIT
cat > "$UNITS/open-ci-hub-bridge-worker.service" <<UNIT
[Unit]
Description=Open CI Hub isolated resource-aware build worker
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=$PYTHON $ROOT/ci_bridge.py worker --workers 2 --poll-seconds 10
Environment=PYTHONUNBUFFERED=1
Environment=OPEN_CI_BRIDGE_MANIFEST=$MANIFEST
Restart=on-failure
RestartSec=15
KillSignal=SIGINT
TimeoutStopSec=45
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
MemoryHigh=512M
MemoryMax=768M
CPUQuota=80%
UMask=0077

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now open-ci-hub-bridge-poll.service
# Worker is installed and enabled, but can only admit work through ResourceGate.
systemctl --user enable --now open-ci-hub-bridge-worker.service
printf '\nBridge services: \n'
systemctl --user --no-pager is-active open-ci-hub-bridge-poll.service open-ci-hub-bridge-worker.service
printf '\nExisting native runners and legacy CI were not modified.\n'
