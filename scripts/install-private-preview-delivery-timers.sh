#!/usr/bin/env bash
# Opt-in: install disabled provider delivery user services. No approval or jobs.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
python_bin="$(command -v python3)"
units="$HOME/.config/systemd/user"
mkdir -p -m 700 "$units"
for provider in github gitlab; do
  src="$root/${provider}_signed_preview_delivery.py"
  test -f "$src" || { echo "Provider receiver source missing: $src" >&2; exit 1; }
  cat > "$units/open-ci-hub-delivery-${provider}@.service" <<EOF
[Unit]
Description=Open CI Hub private signed ${provider} preview intake for %i
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$root
ExecStart=$python_bin $src poll-once %i
Environment=PATH=/usr/local/bin:/usr/bin:/bin
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
UMask=0077
MemoryMax=768M
CPUQuota=65%
TimeoutStartSec=3min
EOF
  cat > "$units/open-ci-hub-delivery-${provider}@.timer" <<EOF
[Unit]
Description=Poll signed private ${provider} preview mailbox for %i

[Timer]
OnCalendar=*:0/5
OnBootSec=4min
RandomizedDelaySec=20s
Persistent=true
Unit=open-ci-hub-delivery-${provider}@%i.service

[Install]
WantedBy=timers.target
EOF
done
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload
fi
echo "Installed disabled GitHub and GitLab signed preview user timers."
echo "Nothing starts. Review isolated host, attestor and local consent before"
echo "enabling either open-ci-hub-delivery-github@APP.timer or"
echo "open-ci-hub-delivery-gitlab@APP.timer."
