#!/usr/bin/env bash
# Repair ONLY the Galaxy Book Go keyboard interrupt DTB on the exact known
# 8GB SanDisk USB. NEVER touches Samsung's internal UFS or Razer's Kingston.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
USB='/dev/disk/by-id/usb-SanDisk_Cruzer_Blade_2006087663032D727A93-0:0'
SERIAL='2006087663032D727A93'
MOUNT='/run/media/theraeburngroup/bootpart'
DEST="$MOUNT/dtb-6.18.34-stb-qc7+/sc7180-samsung-galaxy-book-go.dtb"
BACKUP="$DEST.pre-gpio104"
PATCH="$ROOT/.local/galaxy-book-go/samsung-keyboard-gpio104.dtb"
SOURCE_SHA='8a89b6efa5d584411fbc21585bdbea4062017fab8d797e65ab20a103a8c6d56a'
PATCH_SHA='2836c32ef4cbb8e6bce37d8f8078ed7f7557ddcd13fe841dbdb720fa59fb6190'
if [[ "$(id -u)" -ne 0 ]]; then
    echo >&2 'Run this script as root from Razer Konsole: sudo bash linux-worker/apply-keyboard-dtb.sh'
    exit 1
fi
[[ -L "$USB" ]] || { echo >&2 'Exact SanDisk disk not attached; refusing.'; exit 1; }
DISK="$(readlink -f "$USB")"
[[ "$DISK" =~ ^/dev/sd[a-z]+$ ]] || { echo >&2 'Unexpected disk mapping; refusing.'; exit 1; }
[[ "$(lsblk -dn -o SERIAL "$DISK" | xargs)" == "$SERIAL" ]] || {
    echo >&2 'SanDisk serial mismatch; refusing.'; exit 1;
}
[[ "$(lsblk -dn -o MODEL "$DISK" | xargs)" == 'Cruzer Blade' ]] || {
    echo >&2 'Wrong USB model; refusing.'; exit 1;
}
BOOTPART="$(readlink -f "$USB-part2")"
[[ "$(findmnt -nro SOURCE -T "$MOUNT")" == "$BOOTPART" ]] || {
    echo >&2 'Boot partition is not mounted at the verified target; refusing.'; exit 1;
}
[[ "$(findmnt -nro OPTIONS -T "$MOUNT")" == ro,* ]] || {
    echo >&2 'Expected initial read-only boot partition; refusing.'; exit 1;
}
[[ -f "$DEST" && -f "$PATCH" ]] || { echo >&2 'Missing source DTB or reviewed patch.'; exit 1; }
echo "$SOURCE_SHA  $DEST" | sha256sum --check
echo "$PATCH_SHA  $PATCH" | sha256sum --check
[[ ! -e "$BACKUP" ]] || {
    echo >&2 'Backup already exists: refusing to overwrite/reapply.'; exit 1;
}
echo 'Exact SanDisk, boot partition, image checksum and patch checksum verified.'
echo 'Updating only the Galaxy Book Go keyboard device tree on the USB.'
REWRITABLE=0
cleanup() {
  if [[ "$REWRITABLE" == 1 ]]; then
     mount -o remount,ro "$MOUNT" || echo >&2 'WARNING: Could not remount USB boot partition read-only!'
  fi
}
trap cleanup EXIT
mount -o remount,rw "$MOUNT"
REWRITABLE=1
cp -p -- "$DEST" "$BACKUP"
echo "$SOURCE_SHA  $BACKUP" | sha256sum --check
TMP="$(mktemp "$MOUNT/dtb-6.18.34-stb-qc7+/.kbd-gpio104.XXXXXXXX")"
install -m 0644 "$PATCH" "$TMP"
echo "$PATCH_SHA  $TMP" | sha256sum --check
mv -- "$TMP" "$DEST"
sync
mount -o remount,ro "$MOUNT"
REWRITABLE=0
echo "$PATCH_SHA  $DEST" | sha256sum --check
echo "$SOURCE_SHA  $BACKUP" | sha256sum --check
echo 'SUCCESS: keyboard interrupt DTB repair applied and USB verified.'
echo 'The original DTB is preserved as .pre-gpio104 on the same USB.'
echo 'This fixes the known IRQ flood; it does NOT yet establish why systemd emergency mode occurred.'
