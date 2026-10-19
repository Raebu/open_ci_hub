#!/usr/bin/env bash
# One-time per-user installation; never enables automatically and never runs as root.
set -euo pipefail
if [ "$(id -u)" -eq 0 ]; then
  echo "Run as the ordinary Garuda desktop user; never sudo this installer." >&2
  exit 1
fi
root="$(cd "$(dirname "$0")/.." && pwd -P)"
python_bin="$(command -v python3)"
units="$HOME/.config/systemd/user"
case "$root" in *'
'*) echo "Newlines in installation path are not supported." >&2; exit 1;; esac
mkdir -p "$units"
umask 077
cat > "$units/open-ci-hub-host-agent.service" <<EOF
[Unit]
Description=Open CI Hub unprivileged Linux host agent
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory="$root"
ExecStart="$python_bin" "$root/host_agent.py" serve
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectKernelTunables=true
ProtectControlGroups=true
ProtectKernelModules=true
LockPersonality=true
UMask=0077

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
echo "User service installed, NOT started. No host applications or privileges changed."
echo "To enable from the host PC: systemctl --user enable --now open-ci-hub-host-agent.service"
echo "To approve one app: python3 host_agent.py allow flatpak org.gnome.Calculator --confirm"
echo "To approve a mobile editor: python3 host_agent.py allow mobile user@example.com --confirm"
