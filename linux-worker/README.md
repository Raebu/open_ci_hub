# Galaxy Book Go: headless ARM64 Open CI Hub worker

Experimental build, prepared 27 October 2026. These staging scripts do NOT erase Windows, format a disk or flash a USB.

The Samsung Snapdragon 7c Gen 2 requires device-specific kernel/device-tree support. Upstream Velvet OS calls support incomplete and recommends **USB boot only** while keeping factory Windows on internal UFS/eMMC to preserve its firmware. A recent third-party Debian port worked for one Wi-Fi model, not necessarily yours.

Sources:
- https://github.com/velvet-os/imagebuilder/blob/main/systems/snapdragon_7c_woa/readme.md
- https://github.com/velvet-os/imagebuilder-testimages/releases/tag/spring-2026-testimages
- https://dev.to/xbill/debian-on-a-samsung-galaxy-book-go-written-on-the-galaxy-book-go-p99

## 0. Save original drivers BEFORE a reset or Linux install
Cancel Windows Reset if it has not started. Confirm model and BIOS in Administrator PowerShell:

    Get-CimInstance Win32_ComputerSystem | Select Manufacturer,Model
    Get-CimInstance Win32_BIOS | Select SMBIOSBIOSVersion

Connect a separate external backup disk and run from the Open CI Hub checkout:

    .\linux-worker\backup-firmware.ps1 -ExternalDrive 'E:\'

Replace E: with the actual backup-drive letter. Verify the resulting DriverStore backup on a different PC. Create Windows recovery media on another USB too. Do NOT use the firmware backup disk as the Linux boot USB.

## 1. Download + verify Galaxy-specific USB image on Razer

    bash linux-worker/download-galaxy-image.sh

This downloads a roughly 1.9 GB experimental Debian 13 ARM64 image to git-ignored .local/galaxy-book-go/ and checks the release SHA-256: 8794e8b99571f448bd9019ade34785727c0cae59bcdffb1aee631d9566e2f094. A matching checksum is not evidence of image security.

## 2. Write to a verified, disposable USB ONLY (not yet done)

Use a DIFFERENT >=16 GB USB stick. Identify its exact path, model, capacity and removable status on Razer:

    lsblk -o NAME,PATH,MODEL,SIZE,TRAN,RM,MOUNTPOINTS

Then, once the correct external stick has been verified and its partitions unmounted, write the image. THIS IS A TEMPLATE; do not run unless output disk identity is independently verified:

    gzip -dc .local/galaxy-book-go/snapdragon_7c_woa-aarch64-trixie-260610.img.gz | sudo dd of=/dev/disk/by-id/usb-REPLACE_WITH_VERIFIED_STICK bs=4M status=progress conv=fsync

Wrong disk selection destroys data, including the Razer's Kingston storage.

## 3. Boot-test USB without touching Windows storage

Plug in charger and USB hub; USB Ethernet recommended. Galaxy Book Go: F2 opens BIOS; F10 selects the UEFI boot device. Experimental images may require turning Secure Boot off. Use USB boot only; do NOT overwrite the internal UFS disk.

On Linux check:

    uname -m
    cat /etc/os-release
    lsblk -o NAME,MODEL,SIZE,TRAN,RM,MOUNTPOINTS
    free -h
    ip link

Verify keyboard, USB, memory and network. Wi-Fi or other components may need Samsung firmware from the DriverStore backup. Some variants will not work.

## 4. Headless config, only after verified Debian ARM64 Linux boot

Copy/clone the Open CI Hub repo to the booted Samsung:

    sudo bash linux-worker/configure-headless.sh --prepare

That installs Python, openssh-server, ca-certificates and curl, with no graphical desktop dependency. Set up the official Linux Tailscale client (https://tailscale.com/kb/1031/install-linux), authenticate privately and prove Razer SSH access. Confirm it still works after a reboot. Then:

    sudo bash linux-worker/configure-headless.sh --check
    sudo bash linux-worker/configure-headless.sh --headless

This switches the default target to multi-user.target (text login) without deleting a desktop or drivers. Roll back using: sudo systemctl set-default graphical.target

Check laptop lid suspend behaviour separately before attempting closed-lid operation.

## 5. Join existing Open CI Hub, only once booted

The old Windows worker token does NOT carry over. On Razer, enrol a new 'linux_arm64' worker identity and issue exactly one new private worker token, then on Samsung:

    sudo bash linux-worker/configure-headless.sh --install-worker
    sudo systemctl status open-ci-edge-worker
    sudo journalctl -u open-ci-edge-worker -n 25 --no-pager

The non-login worker service is limited to python.syntax, json.inspect and hash.benchmark. It is not a general shell or browser automation agent.

Acceptance: fresh online Hub heartbeat; exact job receipt; reboots and remote SSH; working networking; safe CPU/memory policy. Revoke the old Windows token once migration succeeds.

**Not yet done:** physical USB flashing, BIOS changes, confirmed Linux boot, actual device pairing, Linux job execution. Do not claim production-ready status until verified.
