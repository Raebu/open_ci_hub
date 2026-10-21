#!/usr/bin/env bash
# Install inert systemd USER service/timer templates. No auto-preview is enabled.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
python="$(command -v python3)"
target="$HOME/.config/systemd/user"
mkdir -p "$target"
cat > "$target/open-ci-hub-preview@.service" <<EOF
[Unit]
Description=Open CI Hub signed, owner-enrolled preview release queue for %i
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$root
ExecStart=$python $root/signed_preview_queue.py run-once %i
Environment=PATH=/usr/local/bin:/usr/bin:/bin
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
UMask=0077
TimeoutStartSec=10min
EOF
cat > "$target/open-ci-hub-preview@.timer" <<'EOF'
[Unit]
Description=Poll owner-approved signed preview queue for %i

[Timer]
OnCalendar=*:0/5
Persistent=true
RandomizedDelaySec=20s
Unit=open-ci-hub-preview@%i.service

[Install]
WantedBy=timers.target
EOF
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload
fi
echo "Installed DISABLED user service/timer templates."
echo "After key pinning, controlled deployment proof and enable-preview consent:"
echo "  systemctl --user enable --now open-ci-hub-preview@APP.timer"
echo "No production release permissions are granted by this timer."
