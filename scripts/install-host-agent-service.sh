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
# systemd expands '%' specifiers in unit values; reject ambiguous paths.
if [[ "$root:$python_bin" == *$'\n'* || "$root:$python_bin" == *$'\r'* || "$root:$python_bin" == *'%'* || "$root:$python_bin" == *'\'* ]]; then
  echo "Unsupported systemd unit path character." >&2
  exit 1
fi
mkdir -p "$units"
umask 077
unit="$units/open-ci-hub-host-agent.service"
temp="$(mktemp "$units/open-ci-hub-host-agent.XXXXXX.service")"
trap 'rm -f "$temp"' EXIT
cat > "$temp" <<UNIT
[Unit]
Description=Open CI Hub unprivileged Linux host agent
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=$root
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
UNIT
if command -v systemd-analyze >/dev/null 2>&1; then
  systemd-analyze verify "$temp" || {
    echo "Refusing to install invalid systemd unit. Existing service file left intact." >&2
    exit 1
  }
fi
mv -f -- "$temp" "$unit"
trap - EXIT
systemctl --user daemon-reload
echo "User service installed, NOT started. No host applications or privileges changed."
echo "To enable from the host PC: systemctl --user enable --now open-ci-hub-host-agent.service"
echo "To approve one app: python3 host_agent.py allow flatpak org.gnome.Calculator --confirm"
echo "To approve a mobile editor: python3 host_agent.py allow mobile user@example.com --confirm"
