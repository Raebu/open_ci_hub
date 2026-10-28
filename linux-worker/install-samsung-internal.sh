#!/usr/bin/env bash
# Samsung Galaxy Book Go internal Debian boot trial — owner-authorised.
# DESTRUCTIVE ONLY to exact Samsung 119GiB internal UFS /dev/sda.
# NEVER touch Qualcomm firmware LUNs /dev/sdb through /dev/sdf or SanDisk USB.
set -Eeuo pipefail
umask 077
DISK=/dev/sda
USBROOT=/dev/sdg3
USBESP=/dev/sdg1
TARGET=/mnt/oci-samsung-internal
ASSETS=/run/oci-internal-stage
LOG=/run/oci-internal-install.log
log(){ printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }
die(){ log "FAILED: $*"; exit 1; }
rootcheck(){
 [[ $(id -u) == 0 ]] || die 'Requires root'
 [[ $(uname -m) == aarch64 ]] || die 'Wrong CPU architecture'
 [[ $(findmnt -n -o SOURCE /) == "$USBROOT" ]] || die 'Not booted from the expected SanDisk root'
 [[ $(readlink -f /dev/disk/by-id/usb-SanDisk_Cruzer_Blade_2006087663032D727A93-0:0) == /dev/sdg ]] || die 'Correct SanDisk recovery USB not present'
 [[ $(lsblk -dn -o MODEL "$DISK" | xargs) == KM5V8001DM-B622 ]] || die 'Wrong internal drive model'
 [[ $(blockdev --getsize64 "$DISK") == 127758499840 ]] || die 'Wrong internal drive size'
 [[ $(blockdev --getss "$DISK") == 4096 ]] || die 'Wrong internal drive sector size'
 [[ $(findmnt -nr -S "$DISK" -o TARGET) == '' ]] || die 'Internal disk mounted'
 for p in 1 2 3 4 5; do
  [[ -z $(findmnt -nr -S "$DISK$p" -o TARGET) ]] || die "Internal partition $p mounted"
 done
 [[ -f "$ASSETS/Image-6.18.34-stb-qc7+" && -f "$ASSETS/initrd.img-6.18.34-stb-qc7+" && -f "$ASSETS/sc7180-samsung-galaxy-book-go.dtb" ]] || die 'Boot files missing'
 echo '04f22cc028d2230dc18787776666c60eada9105ac37e60ad0e0bf03835187d8b  /run/oci-internal-stage/Image-6.18.34-stb-qc7+' | sha256sum --check --status || die 'Kernel checksum mismatch'
 echo '972d75d748c017eabeca9165759ec31c72e74eba1ec34b7e75936df9b5a1dce6  /run/oci-internal-stage/initrd.img-6.18.34-stb-qc7+' | sha256sum --check --status || die 'Initramfs checksum mismatch'
 echo '2836c32ef4cbb8e6bce37d8f8078ed7f7557ddcd13fe841dbdb720fa59fb6190  /run/oci-internal-stage/sc7180-samsung-galaxy-book-go.dtb' | sha256sum --check --status || die 'Samsung device tree checksum mismatch'
 for cmd in sgdisk blockdev lsblk mkfs.vfat mkfs.btrfs btrfs rsync mount umount grub-script-check blkid sha256sum; do command -v "$cmd" >/dev/null || die "Missing utility $cmd"; done
 test -e /sys/firmware/efi || die 'Not UEFI-booted'
 mountpoint -q "$TARGET" && die 'Destination already mounted'
 log 'PREFLIGHT PASS: exact Samsung UFS 119GiB and SanDisk recovery, three boot checksums.'
}
install_local(){
 rootcheck
 log 'IRREVERSIBLE: overwriting Samsung Windows partition map now; only /dev/sda affected.'
 # Wipe only internal Samsung user-data LUN. Other Qualcomm firmware LUNs left unchanged.
 sgdisk --zap-all "$DISK"
 sgdisk --clear -a 256 -n 1:256:+1024M -t 1:EF00 -c 1:DEBIAN_EFI -n 2:0:0 -t 2:8300 -c 2:DEBIAN_ROOT "$DISK"
 partprobe "$DISK"
 udevadm settle
 [[ -b /dev/sda1 && -b /dev/sda2 ]] || die 'New internal partitions missing'
 log 'GPT created: 1GiB FAT32 EFI + remaining Btrfs root.'
 mkfs.vfat -F 32 -n DEBIANEFI /dev/sda1
 mkfs.btrfs -f -L DEBIANROOT /dev/sda2
 mkdir -p "$TARGET"
 mount -t btrfs -o noatime,compress=zstd /dev/sda2 "$TARGET"
 log 'Copying Debian userspace from running SanDisk USB (USB stays untouched).'
 # -x avoids virtual and USB mount contents; preserve ownership, ACLs, xattrs and hardlinks.
 rsync -aHAXx --numeric-ids --exclude='/swap/file.0' --exclude='/boot/*' \
  --exclude='/dev/*' --exclude='/proc/*' --exclude='/sys/*' --exclude='/run/*' \
  --exclude='/tmp/*' --exclude='/mnt/*' --exclude='/media/*' \
  / "$TARGET/"
 mkdir -p "$TARGET/boot" "$TARGET/boot/efi" "$TARGET/proc" "$TARGET/sys" "$TARGET/dev" "$TARGET/run" "$TARGET/tmp"
 chmod 1777 "$TARGET/tmp"
 mount -t vfat /dev/sda1 "$TARGET/boot"
 log 'Creating on-ESP boot files and loading original known-working Samsung GRUB EFI loader.'
 install -m 0644 "$ASSETS/Image-6.18.34-stb-qc7+" "$TARGET/boot/Image-6.18.34-stb-qc7+"
 install -m 0644 "$ASSETS/initrd.img-6.18.34-stb-qc7+" "$TARGET/boot/initrd.img-6.18.34-stb-qc7+"
 install -m 0644 "$ASSETS/sc7180-samsung-galaxy-book-go.dtb" "$TARGET/boot/sc7180-samsung-galaxy-book-go.dtb"
 mkdir -p /run/oci-recovery-efi
 mount -t vfat -o ro "$USBESP" /run/oci-recovery-efi
 cp -a /run/oci-recovery-efi/EFI "$TARGET/boot/"
 umount /run/oci-recovery-efi
 mkdir -p "$TARGET/boot/grub" "$TARGET/boot/EFI/debian" "$TARGET/boot/EFI/BOOT"
 cat > "$TARGET/boot/EFI/debian/grub.cfg" <<'CFG'
insmod part_gpt
insmod fat
search --no-floppy --label --set=root DEBIANEFI
set prefix=($root)/grub
configfile ($root)/grub/grub.cfg
CFG
 cp "$TARGET/boot/EFI/debian/grub.cfg" "$TARGET/boot/EFI/BOOT/grub.cfg"
 ROOT_UUID=$(blkid -s UUID -o value /dev/sda2)
 [[ -n "$ROOT_UUID" ]] || die 'No internal Btrfs UUID'
 cat > "$TARGET/boot/grub/grub.cfg" <<CFG
set timeout=10
set default=0
menuentry 'Debian GNU/Linux 13 - Samsung Galaxy Book Go INTERNAL' {
 insmod part_gpt
 insmod fat
 search --no-floppy --label --set=root DEBIANEFI
 linux /Image-6.18.34-stb-qc7+ console=tty0 root=UUID=$ROOT_UUID rootfstype=btrfs rootwait ro net.ifnames=0 noresume apparmor=0 efi=novamap,noruntime clk_ignore_unused pd_ignore_unused deferred_probe_timeout=30
 devicetree /sc7180-samsung-galaxy-book-go.dtb
 initrd /initrd.img-6.18.34-stb-qc7+
}
CFG
 cat > "$TARGET/etc/fstab" <<'FSTAB'
LABEL=DEBIANROOT / btrfs defaults,noatime,compress=zstd 0 0
LABEL=DEBIANEFI /boot vfat umask=0077 0 2
FSTAB
 # The source USB created a swap file. It is not copied, preventing stale swap references.
 grep -q '^LABEL=DEBIANROOT' "$TARGET/etc/fstab" || die 'fstab not written'
 grub-script-check "$TARGET/boot/grub/grub.cfg"
 grub-script-check "$TARGET/boot/EFI/debian/grub.cfg"
 sha256sum "$TARGET/boot/Image-6.18.34-stb-qc7+" "$TARGET/boot/initrd.img-6.18.34-stb-qc7+" "$TARGET/boot/sc7180-samsung-galaxy-book-go.dtb" | tee -a "$LOG"
 sync
 sgdisk -v "$DISK" | tee -a "$LOG"
 log "INSTALL FILES COMPLETE: root UUID $ROOT_UUID; internal bootloader installed."
 log 'IMPORTANT: still running from USB. Do not remove USB until final unmount/verification.'
}
case "${1:-}" in
 --preflight) rootcheck;;
 --install) install_local;;
 *) echo >&2 'Usage: bash install-samsung-internal.sh --preflight|--install'; exit 2;;
esac
