#!/usr/bin/env bash
# Samsung Galaxy Book Go: run restricted Open CI Hub worker continuously.
# Prevents idle/lid suspend while retaining screen locking and display blanking.
# Does not change graphical session, shutdown/reboot keys, networking or disks.
set -Eeuo pipefail

test "$(id -u)" = 0 || { echo 'Must run as root' >&2; exit 1; }
test "$(hostname)" = samsung-book-go || { echo 'Wrong host' >&2; exit 1; }
test "$(uname -m)" = aarch64 || { echo 'Wrong architecture' >&2; exit 1; }
test "$(findmnt -n -o SOURCE /)" = /dev/sda2 || { echo 'Wrong boot volume' >&2; exit 1; }
test "$(lsblk -dn -o MODEL /dev/sda | xargs)" = KM5V8001DM-B622 || { echo 'Wrong drive' >&2; exit 1; }
systemctl is-active --quiet tailscaled
systemctl is-active --quiet open-ci-edge-worker

install -d -m 0755 /etc/systemd/logind.conf.d /etc/systemd/sleep.conf.d

LOGIND=/etc/systemd/logind.conf.d/90-open-ci-hub-stay-awake.conf
SLEEP=/etc/systemd/sleep.conf.d/90-open-ci-hub-stay-awake.conf
for f in "$LOGIND" "$SLEEP"; do
  if test -e "$f"; then
    echo "Existing configuration '$f'; refusing to overwrite" >&2
    exit 1
  fi
done

cat > "$LOGIND" <<'CFG'
[Login]
IdleAction=ignore
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
HandleSuspendKey=ignore
HandleHibernateKey=ignore
CFG

cat > "$SLEEP" <<'CFG'
[Sleep]
AllowSuspend=no
AllowHibernation=no
AllowHybridSleep=no
AllowSuspendThenHibernate=no
CFG

chmod 0644 "$LOGIND" "$SLEEP"
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target
systemctl reload systemd-logind
systemctl enable NetworkManager ssh tailscaled open-ci-edge-worker >/dev/null

printf '\n--- Hub worker ---\n'
systemctl show open-ci-edge-worker -p ActiveState -p UnitFileState -p User -p MemoryMax -p CPUQuotaPerSecUSec --no-pager
printf '\n--- Sleep targets ---\n'
for x in sleep suspend hibernate hybrid-sleep suspend-then-hibernate; do
  printf '%s: ' "$x"
  systemctl is-enabled "$x.target" || :
done
printf '\n--- Login settings ---\n'
systemd-analyze cat-config systemd/logind.conf | grep -E '^(IdleAction=|HandleLidSwitch|HandleSuspendKey|HandleHibernateKey)' | tail -7
printf '\n--- Network and worker still up ---\n'
systemctl is-active NetworkManager ssh tailscaled open-ci-edge-worker
echo 'STAY_AWAKE_CONFIG_APPLIED'
