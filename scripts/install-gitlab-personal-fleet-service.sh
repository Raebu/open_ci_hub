#!/usr/bin/env bash
# Install but never auto-enable authenticated personal GitLab repository scanning.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
python_bin="$(command -v python3)"
units="$HOME/.config/systemd/user"
mkdir -p "$units"
cat > "$units/open-ci-hub-gitlab-personal@.service" <<EOF
[Unit]
Description=Open CI Hub personal GitLab review-only fleet for %i
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory="$root"
ExecStart="$python_bin" "$root/gitlab_fleet.py" sync-personal %i --apply --limit 5
Environment=PATH=/usr/local/bin:/usr/bin:/bin
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
UMask=0077
TimeoutStartSec=15min
EOF
cat > "$units/open-ci-hub-gitlab-personal@.timer" <<'EOF'
[Unit]
Description=Hourly personal GitLab native CI review scan

[Timer]
OnCalendar=hourly
OnBootSec=3min
RandomizedDelaySec=5min
Persistent=true
Unit=open-ci-hub-gitlab-personal@%i.service

[Install]
WantedBy=timers.target
EOF
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload
fi
echo "Installed personal GitLab fleet service but DID NOT activate it."
echo "Confirm the authenticated username and approve separately:"
echo "  glab api user"
echo "  python3 gitlab_fleet.py authorize-personal USERNAME --confirm"
echo "Then explicitly enable the hourly timer:"
echo "  systemctl --user enable --now open-ci-hub-gitlab-personal@USERNAME.timer"
