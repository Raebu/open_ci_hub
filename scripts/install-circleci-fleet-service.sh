#!/usr/bin/env bash
# Install but do not enable the review-only CircleCI fleet timer.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
python_bin="$(command -v python3)"
units="$HOME/.config/systemd/user"
mkdir -p "$units"
cat > "$units/open-ci-hub-circleci-fleet@.service" <<EOF
[Unit]
Description=Open CI Hub CircleCI machine fleet review scan for %i
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory="$root"
ExecStart="$python_bin" "$root/circleci_fleet.py" sync %i --apply --limit 5
Environment=PATH=/usr/local/bin:/usr/bin:/bin
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
UMask=0077
TimeoutStartSec=15min
EOF
cat > "$units/open-ci-hub-circleci-fleet@.timer" <<'EOF'
[Unit]
Description=Hourly CircleCI native machine CI migration scan

[Timer]
OnCalendar=hourly
OnBootSec=3min
RandomizedDelaySec=5min
Persistent=true
Unit=open-ci-hub-circleci-fleet@%i.service

[Install]
WantedBy=timers.target
EOF
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload
fi
echo "CircleCI fleet timer INSTALLED but not enabled."
echo "Authorize a resource class first: python3 circleci_fleet.py authorize YOUR_GITHUB_OWNER --resource-class namespace/class --confirm"
echo "Then enable: systemctl --user enable --now open-ci-hub-circleci-fleet@YOUR_GITHUB_OWNER.timer"
