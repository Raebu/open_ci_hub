#!/usr/bin/env bash
# Install only: never enable or start the GitHub-to-host executor silently.
set -euo pipefail
if [[ "${EUID}" -eq 0 ]]; then
  echo 'Run as the normal Garuda desktop user, not root.' >&2
  exit 1
fi
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="$(command -v python3)"
units="$HOME/.config/systemd/user"
mkdir -p "$units"
cat > "$units/open-ci-hub-github-bridge.service" <<EOF
[Unit]
Description=Open CI Hub locally approved GitHub host tasks
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$root
ExecStart=$python_bin $root/github_host_bridge.py run-once
NoNewPrivileges=true
UMask=0077
TimeoutStartSec=350
CPUQuota=100%
MemoryMax=1536M
TasksMax=128
EOF
cat > "$units/open-ci-hub-github-bridge.timer" <<'EOF'
[Unit]
Description=Check for locally approved Open CI Hub GitHub tasks

[Timer]
OnBootSec=2min
OnUnitInactiveSec=1min
AccuracySec=15s
Unit=open-ci-hub-github-bridge.service

[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload
echo 'GitHub bridge user timer installed but DISABLED. Nothing was executed.'
echo 'After verifying a private control repository and the configuration:'
echo 'systemctl --user enable --now open-ci-hub-github-bridge.timer'
