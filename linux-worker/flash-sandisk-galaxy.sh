#!/usr/bin/env bash
# Flash the upstream-verified, experimental Galaxy Book Go image to ONE
# exact 8GB SanDisk stick. Refuse everything else. All data on this stick
# will be destroyed. Never touch Kingston XS1000 or internal NVMe.
set -Eeuo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
IMAGE="$REPO/.local/galaxy-book-go/snapdragon_7c_woa-aarch64-trixie-260610.img.gz"
EXPECTED_SHA="8794e8b99571f448bd9019ade34785727c0cae59bcdffb1aee631d9566e2f094"
TARGET="/dev/disk/by-id/usb-SanDisk_Cruzer_Blade_2006087663032D727A93-0:0"
EXPECTED_SERIAL="2006087663032D727A93"
EXPECTED_IMAGE_BYTES=4081057792

if [[ "$(id -u)" -ne 0 ]]; then
    echo >&2 "Administrative raw-disk access is required."
    echo >&2 "Run locally on the Razer: sudo bash linux-worker/flash-sandisk-galaxy.sh"
    exit 1
fi
for cmd in lsblk sha256sum gzip dd cmp readlink blockdev sgdisk sync python3; do
    command -v "$cmd" >/dev/null || { echo >&2 "Missing: $cmd"; exit 1; }
done
[[ -f "$IMAGE" ]] || { echo >&2 "Verified image not staged: $IMAGE"; exit 1; }
[[ -L "$TARGET" ]] || { echo >&2 "Exact SanDisk by-id link absent; refuse to proceed."; exit 1; }

DISK="$(readlink -f -- "$TARGET")"
[[ "$DISK" =~ ^/dev/sd[a-z]+$ ]] || {
    echo >&2 "Unexpected by-id block device ($DISK). Refusing."; exit 1;
}
[[ -b "$DISK" ]] || { echo >&2 "Disk not a block device."; exit 1; }
SERIAL="$(lsblk -dn -o SERIAL "$DISK" | xargs)"
MODEL="$(lsblk -dn -o MODEL "$DISK" | xargs)"
TRAN="$(lsblk -dn -o TRAN "$DISK" | xargs)"
RM="$(lsblk -dn -o RM "$DISK" | xargs)"
RO="$(lsblk -dn -o RO "$DISK" | xargs)"
CAPACITY="$(blockdev --getsize64 "$DISK")"
[[ "$SERIAL" == "$EXPECTED_SERIAL" ]] || { echo >&2 "Serial mismatch; refusing."; exit 1; }
[[ "$MODEL" == "Cruzer Blade" && "$TRAN" == "usb" && "$RM" == "1" && "$RO" == "0" ]] || {
    echo >&2 "Removable SanDisk identity mismatch; refusing."; exit 1;
}
[[ "$CAPACITY" -ge "$EXPECTED_IMAGE_BYTES" && "$CAPACITY" -lt 12000000000 ]] || {
    echo >&2 "USB has unexpected size ($CAPACITY); refusing."; exit 1;
}

# Do not unmount anything automatically; every partition must be idle.
if lsblk -nr -o MOUNTPOINTS "$DISK" | grep -q '[^[:space:]]'; then
    echo >&2 "A SanDisk filesystem is mounted. Unmount safely, then retry."
    lsblk -o NAME,MOUNTPOINTS "$DISK"
    exit 1
fi
if swapon --noheadings --show=NAME 2>/dev/null | grep -Fq "$DISK"; then
    echo >&2 "The SanDisk is used for swap; refusing."
    exit 1
fi

echo "Verifying trusted-download integrity (may take a minute)..."
echo "$EXPECTED_SHA  $IMAGE" | sha256sum --check

# The 4,081,057,792-byte source image uses a GPT; confirm its header
# describes the exact length expected (not a mistaken distro image).
python3 - "$IMAGE" "$EXPECTED_IMAGE_BYTES" <<'PY'
import gzip, struct, sys
with gzip.open(sys.argv[1], "rb") as stream:
    data = stream.read(1024)
assert data[512:520] == b'EFI PART', 'Not a GPT image'
assert (struct.unpack_from('<Q', data, 512+32)[0] + 1)*512 == int(sys.argv[2]), 'Unexpected GPT length'
print('Source GPT geometry verified')
PY

echo
echo "EXACT TARGET: $DISK / $MODEL / $SERIAL"
echo "TARGET CAPACITY: $CAPACITY bytes"
echo "IMAGE: $IMAGE"
echo "This ERASES the current Garuda installation USB contents permanently."
read -r -p "Type FLASH-SANDISK to erase ONLY this USB stick: " ANSWER
[[ "$ANSWER" == "FLASH-SANDISK" ]] || { echo "Cancelled: nothing written."; exit 1; }

# Check identity once more immediately before writing.
[[ "$(readlink -f -- "$TARGET")" == "$DISK" ]] || { echo >&2 "USB mapping changed. Aborting."; exit 1; }
[[ "$(lsblk -dn -o SERIAL "$DISK" | xargs)" == "$EXPECTED_SERIAL" ]] || { echo >&2 "Serial changed. Aborting."; exit 1; }
echo "Flashing Debian 13 ARM64 experimental boot image ..."
gzip -dc "$IMAGE" | dd of="$TARGET" bs=4M iflag=fullblock conv=fsync status=progress
sync
echo "Reading entire image back to verify exact byte-for-byte match (can take several minutes)..."
gzip -dc "$IMAGE" | cmp -n "$EXPECTED_IMAGE_BYTES" - "$TARGET"
echo "Read-back verified."

# The upstream image GPT backup is at its 4GB end. Move only its
# secondary GPT header/table to the physical end of the larger 8GB USB.
# Does not enlarge any partition or modify the internal disk.
sgdisk -e "$DISK"
sgdisk -v "$DISK"
sync
lsblk -o NAME,PATH,SIZE,MODEL,TRAN,RM,FSTYPE,MOUNTPOINTS "$DISK"
echo "SUCCESS: the SanDisk USB now contains the tested-image candidate."
echo "Do not overwrite the Samsung's Windows internal storage. Test USB boot first."
