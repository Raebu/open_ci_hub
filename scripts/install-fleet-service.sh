#!/usr/bin/env bash
# Install an opt-in hourly user timer; does not approve scopes or start jobs.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
python_bin="$(command -v python3)"
units="$HOME/.config/systemd/user"
mkdir -p "$units"
cat > "$units/open-ci-hub-fleet@.service" <<EOF
[Unit]
Description=Open CI Hub trusted GitHub fleet scan for %i
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory="$root"
ExecStart="$python_bin" "$root/fleet.py" sync %i --apply --limit 5
Environment=PATH=/usr/local/bin:/usr/bin:/bin
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
UMask=0077
TimeoutStartSec=15min
EOF
cat > "$units/open-ci-hub-fleet@.timer" <<'EOF'
[Unit]
Description=Check opted-in repositories hourly for self-hosted CI migration

[Timer]
OnCalendar=hourly
OnBootSec=3min
RandomizedDelaySec=5min
Persistent=true
Unit=open-ci-hub-fleet@%i.service

[Install]
WantedBy=timers.target
EOF
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload
fi
echo "Timer installed (not enabled). Approve an exact scope using:"
echo "  python3 fleet.py authorize YOUR_GITHUB_OWNER --confirm"
echo "Then enable a scope with:"
echo "  systemctl --user enable --now open-ci-hub-fleet@YOUR_GITHUB_OWNER.timer"
echo "The timer only creates review PRs for an explicitly approved scope. It never merges."
