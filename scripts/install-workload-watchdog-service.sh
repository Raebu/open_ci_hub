#!/usr/bin/env bash
# Install opt-in user timer; does not enable or start anything automatically.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
python_bin="$(command -v python3)"
units="$HOME/.config/systemd/user"
mkdir -p "$units"
cat > "$units/open-ci-hub-workload-watchdog.service" <<EOF
[Unit]
Description=Open CI Hub laptop safety and managed job reconciliation

[Service]
Type=oneshot
WorkingDirectory=$root
ExecStart=$python_bin $root/app_runtime.py watchdog
Environment=PATH=/usr/local/bin:/usr/bin:/bin
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
UMask=0077
TimeoutStartSec=120
EOF
cat > "$units/open-ci-hub-workload-watchdog.timer" <<'EOF'
[Unit]
Description=Run Open CI Hub safety checks each minute

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
AccuracySec=15s
Persistent=true
Unit=open-ci-hub-workload-watchdog.service

[Install]
WantedBy=timers.target
EOF
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload
fi
echo "Workload watchdog installed (DISABLED). No worker or service was started."
echo "When tested and approved: systemctl --user enable --now open-ci-hub-workload-watchdog.timer"
