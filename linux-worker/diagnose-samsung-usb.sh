#!/usr/bin/env bash
# Read ONLY the reattached Galaxy Book Go experimental USB boot logs.
# No fsck repairs, unmounts, formatting, privilege changes or writes to USB.
set -euo pipefail
EXPECTED_SERIAL='2006087663032D727A93'
USB='/dev/disk/by-id/usb-SanDisk_Cruzer_Blade_2006087663032D727A93-0:0'
LOGDIR='/run/media/theraeburngroup/rootpart/var/log/journal'
if [[ "$(id -u)" != 0 ]]; then
  echo >&2 'Administrator read access is necessary for protected system journal files.'
  echo >&2 'Run on Razer in a real Konsole: sudo bash linux-worker/diagnose-samsung-usb.sh'
  exit 1
fi
[[ -L "$USB" ]] || { echo >&2 'Verified SanDisk missing; no logs read.'; exit 1; }
DISK="$(readlink -f "$USB")"
[[ "$(lsblk -dn -o SERIAL "$DISK" | xargs)" == "$EXPECTED_SERIAL" ]] || {
  echo >&2 'USB serial mismatch; refused.'; exit 1;
}
[[ "$(findmnt -nro OPTIONS -S "$DISK"3 2>/dev/null)" == *ro* ]] || {
  echo >&2 'Root filesystem must be mounted READ ONLY by the Razer before inspection.'; exit 1;
}
[[ -d "$LOGDIR" ]] || { echo >&2 'Journal path not mounted; refused.'; exit 1; }
echo '=== Galaxy Book Go previous USB boots ==='
journalctl --directory="$LOGDIR" --list-boots --no-pager | tail -25 || true
echo '=== Last-boot errors, excluding the repeated I2C HID flood ==='
journalctl --directory="$LOGDIR" -b 0 --no-pager -p err -o short-monotonic |
  grep -Ev 'i2c_hid_get_input|incomplete report' | tail -70 || true
echo '=== Mount/service/systemd startup failure matches ==='
journalctl --directory="$LOGDIR" -b 0 --no-pager -g 'Failed to|Dependency failed|emergency mode|Timed out waiting|mounting|fsck|cannot mount|Mount process exited|failed with result' -o short-monotonic |
  grep -Ev 'i2c_hid_get_input|incomplete report' | tail -80 || true
echo '=== USB labels, UUID and free space ==='
lsblk -o NAME,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS "$DISK"
df -h /run/media/theraeburngroup/rootpart /run/media/theraeburngroup/bootpart
echo 'Read-only diagnosis finished. Do not post private journal lines containing passwords or tokens.'
