#!/usr/bin/env bash
# Explicit owner enrolment of the restricted merged-branch GitHub cleanser.
set -euo pipefail
if [[ "${1:-}" != "--confirm" || $# -ne 1 ]]; then
  echo "Run only after review: bash scripts/install-branch-cleanser.sh --confirm" >&2
  exit 2
fi
if [[ "$(id -u)" == 0 ]]; then echo "Do not install as root." >&2; exit 2; fi
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [[ "$root" =~ [[:space:]%] ]]; then
  echo "Checkout path unsupported in systemd unit; refusing." >&2; exit 3
fi
if [[ "$(git -C "$root" branch --show-current)" != "main" ]]; then
  echo "Install only from the owner review-approved main checkout." >&2; exit 3
fi
if [[ -n "$(git -C "$root" status --porcelain --untracked-files=no)" ]]; then
  echo "Main checkout is dirty; refusing install." >&2; exit 3
fi
python3 "$root/branch_cleanser.py" > /dev/null
unit_dir="$HOME/.config/systemd/user"
mkdir -p "$unit_dir"
chmod 700 "$unit_dir"
service="$unit_dir/open-ci-hub-branch-cleanser.service"
timer="$unit_dir/open-ci-hub-branch-cleanser.timer"
if [[ -e "$service" || -L "$service" || -e "$timer" || -L "$timer" ]]; then
  echo "Existing cleanser service detected; refusing to overwrite." >&2; exit 3
fi
cat > "$service" <<EOF
[Unit]
Description=Open CI Hub verified merged-branch cleanser (archives before deleting)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$root
ExecStart=/usr/bin/flock -n %t/open-ci-hub-branch-cleanser.lock /usr/bin/python3 $root/branch_cleanser.py --apply --confirm
Environment=PYTHONDONTWRITEBYTECODE=1
Nice=15
NoNewPrivileges=true
PrivateTmp=true
UMask=0077
MemoryMax=256M
CPUQuota=15%
TimeoutStartSec=15min
EOF
cat > "$timer" <<EOF
[Unit]
Description=Daily verified merged-branch cleanup

[Timer]
OnCalendar=*-*-* 03:45:00
OnBootSec=8min
RandomizedDelaySec=15min
Persistent=true
Unit=open-ci-hub-branch-cleanser.service

[Install]
WantedBy=timers.target
EOF
chmod 600 "$service" "$timer"
systemctl --user daemon-reload
systemctl --user enable --now open-ci-hub-branch-cleanser.timer
echo "Installed daily archive-first merged-branch cleanser. Timer active; no branch deleted during installation."
