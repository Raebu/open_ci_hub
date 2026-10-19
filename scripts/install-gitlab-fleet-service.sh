#!/usr/bin/env bash
# Install systemd user timer without enabling it or authorising a GitLab group.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
python_bin="$(command -v python3)"
units="$HOME/.config/systemd/user"
mkdir -p "$units"
cat > "$units/open-ci-hub-gitlab-fleet@.service" <<EOF
[Unit]
Description=Open CI Hub GitLab group review-only CI migration for %i
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory="$root"
ExecStart="$python_bin" "$root/gitlab_fleet.py" sync %i --apply --limit 5
Environment=PATH=/usr/local/bin:/usr/bin:/bin
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
UMask=0077
TimeoutStartSec=15min
EOF
cat > "$units/open-ci-hub-gitlab-fleet@.timer" <<'EOF'
[Unit]
Description=Hourly GitLab group CI migration review scan

[Timer]
OnCalendar=hourly
OnBootSec=3min
RandomizedDelaySec=5min
Persistent=true
Unit=open-ci-hub-gitlab-fleet@%i.service

[Install]
WantedBy=timers.target
EOF
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload
fi
echo "GitLab fleet timer installed but NOT enabled."
echo "Approve a group first: python3 gitlab_fleet.py authorize YOUR_GROUP --confirm"
echo "Enable only when ready: systemctl --user enable --now open-ci-hub-gitlab-fleet@YOUR_GROUP.timer"
