#!/usr/bin/env bash
set -euo pipefail
ROOT="/mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub"
USER_DIR="$HOME/.config/systemd/user"
mkdir -p "$USER_DIR"
install -m 0644 "$ROOT/linux-worker/raeburn-command-worker.service" "$USER_DIR/raeburn-command-worker.service"
install -m 0644 "$ROOT/linux-worker/raeburn-command-daily.service" "$USER_DIR/raeburn-command-daily.service"
install -m 0644 "$ROOT/linux-worker/raeburn-command-backup.service" "$USER_DIR/raeburn-command-backup.service"
cat > "$USER_DIR/raeburn-command-worker.timer" <<'TIMER'
[Unit]
Description=RaeburnAI process one approved local mission step

[Timer]
OnBootSec=90s
OnUnitInactiveSec=45s
RandomizedDelaySec=8s
AccuracySec=15s
Unit=raeburn-command-worker.service

[Install]
WantedBy=timers.target
TIMER
cat > "$USER_DIR/raeburn-command-daily.timer" <<'TIMER'
[Unit]
Description=RaeburnAI daily evidence-based operational briefing

[Timer]
OnCalendar=*-*-* 08:30:00
AccuracySec=1min
RandomizedDelaySec=1min
Persistent=true
Unit=raeburn-command-daily.service

[Install]
WantedBy=timers.target
TIMER
cat > "$USER_DIR/raeburn-command-backup.timer" <<'TIMER'
[Unit]
Description=RaeburnAI independent, integrity-tested local mission backups

[Timer]
OnCalendar=*-*-* 03:45:00
AccuracySec=1min
RandomizedDelaySec=2min
Persistent=true
Unit=raeburn-command-backup.service

[Install]
WantedBy=timers.target
TIMER
chmod 0644 "$USER_DIR/raeburn-command-worker.timer" "$USER_DIR/raeburn-command-daily.timer" "$USER_DIR/raeburn-command-backup.timer"
systemctl --user daemon-reload
systemctl --user enable --now raeburn-command-worker.timer raeburn-command-daily.timer raeburn-command-backup.timer
systemctl --user start raeburn-command-worker.service
systemctl --user show raeburn-command-worker.service -p Result -p ExecMainStatus --no-pager
systemctl --user list-timers --all --no-pager raeburn-command-worker.timer raeburn-command-daily.timer raeburn-command-backup.timer
