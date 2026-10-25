#!/usr/bin/env bash
# Only starts the fixed-job daemon; does not authorise new work or enroll devices.
set -euo pipefail
if [[ $# -ne 1 || "$1" != "--confirm" ]]; then
  echo "Explicit --confirm is required. Review docs/BACKGROUND-AUTONOMY.md."
  exit 2
fi
if [[ "$(id -u)" == "0" ]]; then echo "Never install as root."; exit 2; fi
root="$(cd "$(dirname "$0")/.." && pwd -P)"
case "$root" in *"%"*|*" "*|*"\""*) echo "Unsupported checkout path."; exit 2;; esac
if [[ ! -r "$root/autonomy_daemon.py" || ! -r "$root/autonomy_cli.py" ]]; then
  echo "Autonomy source missing."; exit 2
fi
unit_dir="$HOME/.config/systemd/user"
unit="$unit_dir/open-ci-hub-autonomy.service"
mkdir -p "$unit_dir"
chmod 700 "$unit_dir"
if [[ -e "$unit" || -L "$unit" ]]; then
  echo "Service already exists. Refusing to overwrite it."
  exit 3
fi
cat > "$unit" <<EOF
[Unit]
Description=Open CI Hub background approved-mission worker
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $root/autonomy_daemon.py --interval 60
WorkingDirectory=$root
Restart=on-failure
RestartSec=30
Nice=15
CPUQuota=20%
MemoryMax=300M
MemoryHigh=240M
MemorySwapMax=0
TasksMax=80
NoNewPrivileges=yes
Environment=PYTHONDONTWRITEBYTECODE=1

[Install]
WantedBy=default.target
EOF
chmod 600 "$unit"
systemctl --user daemon-reload
systemctl --user enable --now open-ci-hub-autonomy.service
systemctl --user --no-pager status open-ci-hub-autonomy.service
