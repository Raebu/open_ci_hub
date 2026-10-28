#!/usr/bin/env bash
# Read-only Samsung Galaxy Book Go recovery and Windows backups to Kingston.
# Source is NEVER mounted writable or repartitioned.
set -Eeuo pipefail
umask 077
B="/mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub/.local/samsung-preservation-20261028"
HOST="100.77.117.120"
LOG="$B/ntfs-backup-status.log"
exec >>"$LOG" 2>&1
echo "START $(date -u +%FT%TZ)"
test "$(stat -c %a "$B")" = 700
test -s "$B/samsung-gpt.backup"
check_disk() {
 timeout 15 tailscale ssh root@"$HOST" \
  'set -eu; test "$(blockdev --getss /dev/sda)" -eq 4096; test "$(blockdev --getsize64 /dev/sda)" -eq 127758499840; test "$(lsblk -dn -o MODEL /dev/sda | xargs)" = KM5V8001DM-B622; ! findmnt -rn -S /dev/sda3; ! findmnt -rn -S /dev/sda5; echo DISK_VERIFIED'
}
check_disk
for part in 5 3; do
 case "$part" in
  5) out="$B/samsung-sda5-factory-recovery.ntfsclone.zst"; bytes=11265900544;;
  3) out="$B/samsung-sda3-windows.ntfsclone.zst"; bytes=115309805568;;
 esac
 echo "BEGIN sda$part $(date -u +%FT%TZ)"
 if test -f "$out"; then
  zstd -q -t "$out"
  echo "SKIP already complete $out"
  continue
 fi
 check_disk
 test ! -e "$out.part" || rm -f "$out.part"
 timeout 7200 tailscale ssh root@"$HOST" \
  "set -eu; test \"\$(blockdev --getsize64 /dev/sda$part)\" -eq $bytes; ! findmnt -rn -S /dev/sda$part; ntfsclone --save-image --output - --quiet /dev/sda$part" \
  | zstd -T1 -1 -q -o "$out.part"
 zstd -q -t "$out.part"
 mv "$out.part" "$out"
 chmod 600 "$out"
 sha256sum "$out" > "$out.sha256"
 echo "COMPLETE sda$part $(date -u +%FT%TZ) compressed_bytes=$(stat -c%s "$out")"
done
echo "ALL_NTFS_BACKUPS_VERIFIED $(date -u +%FT%TZ)"
