#!/usr/bin/env bash
# Install (but do NOT enable) a pressure-aware provider runner starter timer.
# The owner must approve each existing GitHub/GitLab/CircleCI Compose runner.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
python_bin="$(command -v python3)"
units="$HOME/.config/systemd/user"
mkdir -p "$units"
cat > "$units/open-ci-hub-provider-runners.service" <<EOF
[Unit]
Description=Open CI Hub guarded provider runner start admission
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$root
ExecStart=$python_bin $root/provider_runner_control.py schedule
Environment=PATH=/usr/local/bin:/usr/bin:/bin
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
UMask=0077
TimeoutStartSec=120
EOF
cat > "$units/open-ci-hub-provider-runners.timer" <<'EOF'
[Unit]
Description=Periodically admit approved native CI runners if safe

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
RandomizedDelaySec=15s
Persistent=true
Unit=open-ci-hub-provider-runners.service

[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload
echo "Installed disabled-by-default provider admission timer; no containers changed."
echo "Confirm existing containers have restart=no and safe resource caps."
echo "Approve with python3 provider_runner_control.py approve github --confirm (repeat by provider)"
echo "Then enable: systemctl --user enable --now open-ci-hub-provider-runners.timer"
