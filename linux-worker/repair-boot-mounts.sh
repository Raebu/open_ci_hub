#!/usr/bin/env bash
# Temporary USB trial fix for Galaxy Book Go June-2026 test image:
# Kernel lacks EXT4_FS, but /boot is ext4 -> emergency.target.
# Update ONLY two fstab entries to skip automatic boot-partition mounts.
# Preserves Windows, kernel, boot partition and USB filesystem contents.
set -Eeuo pipefail
EXPECTED_SERIAL='2006087663032D727A93'
USB='/dev/disk/by-id/usb-SanDisk_Cruzer_Blade_2006087663032D727A93-0:0'
ROOT='/run/media/theraeburngroup/rootpart'
BOOT='/run/media/theraeburngroup/bootpart'
ORIGINAL_SHA='f84df08b422980836b4e33bc7edede7eca63e91a1b4844f6b47df93a117d1108'
ORIGINAL_BOOT='LABEL=bootpart /boot ext4 defaults,noatime,nodiratime,errors=remount-ro 0 2'
NEW_BOOT='LABEL=bootpart /boot ext4 noauto,nofail,noatime,nodiratime,errors=remount-ro 0 0'
ORIGINAL_EFI='LABEL=EFI /boot/efi vfat umask=0077 0 1'
NEW_EFI='LABEL=EFI /boot/efi vfat noauto,nofail,umask=0077 0 0'
if [[ "$(id -u)" != '0' ]]; then
  echo >&2 'Run in Razer Konsole: sudo bash linux-worker/repair-boot-mounts.sh'
  exit 1
fi
[[ -L "$USB" ]] || { echo >&2 'Expected SanDisk USB is not attached.'; exit 1; }
DEVICE="$(readlink -f -- "$USB")"
[[ "$DEVICE" =~ ^/dev/sd[a-z]+$ ]] || { echo >&2 'Unexpected USB device mapping.'; exit 1; }
[[ "$(lsblk -dn -o SERIAL "$DEVICE" | xargs)" == "$EXPECTED_SERIAL" ]] || {
  echo >&2 'USB serial changed: REFUSE'; exit 1;
}
[[ "$(lsblk -dn -o MODEL "$DEVICE" | xargs)" == 'Cruzer Blade' ]] || {
  echo >&2 'USB model changed: REFUSE'; exit 1;
}
USB_ROOT="$(readlink -f -- "$USB-part3")"
USB_BOOT="$(readlink -f -- "$USB-part2")"
[[ "$(findmnt -nro SOURCE -T "$ROOT")" == "$USB_ROOT" ]] || {
  echo >&2 'Expected SanDisk ROOT partition is not mounted at the read-only inspection path.'; exit 1;
}
[[ "$(findmnt -nro SOURCE -T "$BOOT")" == "$USB_BOOT" ]] || {
  echo >&2 'Expected SanDisk BOOT partition is not mounted at inspection path.'; exit 1;
}
[[ ",$(findmnt -nro OPTIONS -T "$ROOT")," == *,ro,* ]] || {
  echo >&2 'Expected original ROOT to be mounted READ-ONLY.'; exit 1;
}
[[ ",$(findmnt -nro OPTIONS -T "$BOOT")," == *,ro,* ]] || {
  echo >&2 'Expected BOOT to be mounted READ-ONLY.'; exit 1;
}
CONFIG="$BOOT/config-6.18.34-stb-qc7+"
[[ -f "$CONFIG" ]] || { echo >&2 'June-2026 ARM64 kernel config missing.'; exit 1; }
grep -Fqx '# CONFIG_EXT4_FS is not set' "$CONFIG" || {
  echo >&2 'Kernel ext4 configuration changed; refusing this trial-only fix.'; exit 1;
}
FSTAB="$ROOT/etc/fstab"
[[ -f "$FSTAB" ]] || { echo >&2 'No fstab at expected root filesystem.'; exit 1; }
echo "$ORIGINAL_SHA  $FSTAB" | sha256sum --check
grep -Fqx "$ORIGINAL_BOOT" "$FSTAB"
grep -Fqx "$ORIGINAL_EFI" "$FSTAB"
[[ ! -e "$FSTAB.pre-noauto-boot" ]] || {
  echo >&2 'Original backup already present. Refusing repeated application.'; exit 1;
}
echo 'Validated SanDisk identity, Btrfs root, original fstab and EXT4-disabled kernel.'
echo 'Will make /boot and /boot/efi NOAUTO/NOFAIL for one USB boot test.'
echo 'WARNING: Until kernel supports EXT4, do not perform kernel/GRUB upgrades.'
REWRITABLE=0
restore_ro() {
  if [[ "$REWRITABLE" == '1' ]]; then
    mount -o remount,ro "$ROOT" || echo >&2 'WARNING: unable to restore root filesystem read-only.'
  fi
}
trap restore_ro EXIT
mount -o remount,rw "$ROOT"
REWRITABLE=1
cp -p -- "$FSTAB" "$FSTAB.pre-noauto-boot"
echo "$ORIGINAL_SHA  $FSTAB.pre-noauto-boot" | sha256sum --check
python3 - "$FSTAB" "$ORIGINAL_EFI" "$NEW_EFI" "$ORIGINAL_BOOT" "$NEW_BOOT" <<'PY'
import hashlib, os, pathlib, sys, tempfile
path=pathlib.Path(sys.argv[1])
raw=path.read_bytes()
old_efi,new_efi,old_boot,new_boot=sys.argv[2:]
if raw.count(old_efi.encode()+b'\n') != 1 or raw.count(old_boot.encode()+b'\n') != 1:
    raise SystemExit('Expected fstab entries missing; no modified file written')
patched=raw.replace(old_efi.encode()+b'\n',new_efi.encode()+b'\n').replace(
    old_boot.encode()+b'\n',new_boot.encode()+b'\n')
if patched.count(b'noauto,nofail') != 2 or len(patched)<=len(raw):
    raise SystemExit('Patch validation failed')
fd,tmp=tempfile.mkstemp(prefix='.fstab.oci-',dir=str(path.parent))
try:
    with os.fdopen(fd,'wb') as f:
        f.write(patched);f.flush();os.fsync(f.fileno())
    os.chmod(tmp,0o644)
    os.replace(tmp,path)
    os.sync()
except BaseException:
    if os.path.exists(tmp):os.unlink(tmp)
    raise
print('Patched fstab SHA256:',hashlib.sha256(patched).hexdigest())
PY
grep -Fqx "$NEW_BOOT" "$FSTAB"
grep -Fqx "$NEW_EFI" "$FSTAB"
sync
mount -o remount,ro "$ROOT"
REWRITABLE=0
echo "$ORIGINAL_SHA  $FSTAB.pre-noauto-boot" | sha256sum --check
echo 'SUCCESS: SanDisk root fstab repaired for a temporary USB boot trial.'
echo 'No boot/kernel partitions formatted; only two fstab lines changed.'
echo 'BOOT/EFI ARE NOT AUTO-MOUNTED. Do NOT run kernel or GRUB updates yet.'
