#!/usr/bin/env bash
# Install a disabled-by-default five-minute native per-repository runner pool.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
python_bin="$(command -v python3)"
units="$HOME/.config/systemd/user"
mkdir -p "$units"
cat > "$units/open-ci-hub-pool@.service" <<EOF
[Unit]
Description=Open CI Hub native GitHub worker pool for %i
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory="$root"
ExecStart="$python_bin" "$root/runner_pool.py" sync %i --apply --limit 4
Environment=PATH=/usr/local/bin:/usr/bin:/bin
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
UMask=0077
TimeoutStartSec=15min
EOF
cat > "$units/open-ci-hub-pool@.timer" <<'EOF'
[Unit]
Description=Check native GitHub worker demand every five minutes

[Timer]
OnCalendar=*:0/5
OnBootSec=2min
RandomizedDelaySec=15s
Persistent=true
Unit=open-ci-hub-pool@%i.service

[Install]
WantedBy=timers.target
EOF
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload
fi
echo "Pool timer INSTALLED but NOT ENABLED. No runners were registered."
echo "Approve an exact account: python3 runner_pool.py authorize YOUR_GITHUB_OWNER --confirm"
echo "Enable only when ready: systemctl --user enable --now open-ci-hub-pool@YOUR_GITHUB_OWNER.timer"
