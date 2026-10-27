#!/usr/bin/env bash
# Install a user-owned 5-minute edge pool scheduler; never needs root.
# Review .local/edge-pool-monitor.json before running.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MANIFEST="$ROOT/.local/edge-pool-monitor.json"
SYSTEMD_DIR="$HOME/.config/systemd/user"
SERVICE="$SYSTEMD_DIR/open-ci-edge-pool.service"
TIMER="$SYSTEMD_DIR/open-ci-edge-pool.timer"
[[ "$ROOT" == /mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub ]] || {
    echo >&2 'Unexpected Open CI Hub checkout; cannot install a timer in this location.'
    exit 1
}
[[ -f "$MANIFEST" && ! -L "$MANIFEST" ]] || {
    echo >&2 'Create reviewed .local/edge-pool-monitor.json first.'
    exit 1
}
python3 "$ROOT/edge_pool_monitor.py" --manifest "$MANIFEST" --reconcile
mkdir -p "$SYSTEMD_DIR"
if [[ -f "$SERVICE" || -f "$TIMER" ]]; then
    echo >&2 'One of the managed pool units already exists; inspect it rather than overwriting.'
    exit 1
fi
cat > "$SERVICE" <<UNIT
[Unit]
Description=Open CI Hub bounded S22/S25 pooled preflight and safe reconciliation
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$ROOT
ExecStart=/usr/bin/python3 $ROOT/edge_pool_monitor.py --manifest $MANIFEST --reconcile
TimeoutStartSec=90
Nice=15
NoNewPrivileges=true
PrivateTmp=true
MemoryMax=192M
CPUQuota=30%
UNIT
cat > "$TIMER" <<'UNIT'
[Unit]
Description=Check curated project files and safe device failover every five minutes

[Timer]
OnBootSec=90s
OnUnitActiveSec=5min
AccuracySec=45s
Unit=open-ci-edge-pool.service

[Install]
WantedBy=timers.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now open-ci-edge-pool.timer
systemctl --user start open-ci-edge-pool.service
systemctl --user --no-pager status open-ci-edge-pool.timer | head -16
echo 'Auto dispatcher installed; jobs limited to reviewed, explicit manifest and registered device apps.'
echo 'Disable: systemctl --user disable --now open-ci-edge-pool.timer'
