# Samsung Galaxy Book Go native Wi-Fi and edge worker — 29 October 2026

Hardware independently inspected through owner-authenticated Tailscale SSH: Samsung Galaxy Book Go SC7180, Debian 13 ARM64 on internal /dev/sda2, XFCE graphical session, Tailscale hostname samsung-book-go (100.77.117.120).

## Wi-Fi repair
Built-in Qualcomm Wi-Fi was absent despite ath10k_snoc being loaded because Samsung proprietary Qualcomm modem and wireless firmware were missing. The partial Windows DriverStore backup had zero-byte Windows reparse links, not usable binary firmware. Downloaded device-specific public archive NP340XLA.7z from tordona/woa-device-drivers GitHub release samsung-np340xla-drivers; archive SHA256 13261cbbba12911c006d02cd016200a7680714ace960f0f1c2f2ed9a9ed4f90b matched published release.

Extracted and independently verified the two Qualcomm firmware binaries against the exact checksums of a documented working Samsung Debian port:
- qcmpss7180_nm.mbn SHA256 a4016412a5071a95934b0e9db343d51dd8b1cda1252f3b6baae09b34356f02f2 (5,062,656 B).
- wlanmdsp.mbn SHA256 7688eea1c13715289f58c8d379b9f73b81d072415ac141b10b4732d5059f7f4b (3,832,348 B).

Installed only at /lib/firmware/qcom/sc7180/samsung/galaxy-book-go. Modem booted; ath10k showed firmware WLAN.HL.3.2.2.c10-00767; phy0 and wlan0 appeared. NetworkManager Raeburn Wi-Fi profile created from Razer's previously saved owner PSK, securely sent over authenticated Tailscale SSH without printing credentials or storing any secret in Git. NM keyfile mode 0600. DHCP 192.168.1.123/24, gateway 192.168.1.1. Ping 1.1.1.1 explicitly on wlan0: 2/2 success. HTTPS GET explicitly on wlan0: HTTP 200. Wi-Fi preference route metric 50; default ip route get 1.1.1.1 uses wlan0; HTTP 200 without USB interface specified. The S22 USB tether later disappeared from interface status while Wi-Fi stayed connected; true live independent Internet confirmed.

## Self-healing on boot
Installed and enabled linux-worker/samsung-wifi-start.sh (on device /usr/local/sbin/oci-samsung-wifi-start), oci-samsung-wifi.service and oci-samsung-wifi.timer. The guarded systemd service checks exact hardware and firmware SHA256, waits for rmtfs/tqftpserv, starts modem if offline and restarts it no more than once if Wi-Fi phy fails to appear. First service execution succeeded; five-minute timer enabled. Original NetworkManager autoconnect is retained. No bootloader, Windows recovery or device-tree modifications made.

## Edge compute and memory
Preserved loopback-only RaeburnAI Qwen3.5 2B at port 8879 (health OK). Disabled automatic startup of redundant smaller oci-samsung-qwen 0.6B model without deleting it, freeing about 600 MB. Installed systemd-zram-generator with persistent 1.7 GB zram swap; does not increase physical RAM. Kept strict per-job max 256 MB and 128 MB real job budget. The on-device portable_edge_worker.py permits a lower 896 MB available-RAM threshold only when explicitly selected and matched to Samsung Galaxy Book Go on Linux ARM64; all other devices retain >=1,536 MB. Samsung-only Razer broker reserve 768 MB, and fresh signed worker heartbeat is mandatory.

Actual Razer app_runtime.py edge-list returned samsung-linux-edge approved and online with about 1,070 MB MemAvailable. A live, specifically targeted json.inspect job completed in one attempt. The result SHA256 was checked independently; the temporary submit credential was revoked immediately after testing.

Lid handling was already configured with HandleLidSwitch=ignore; suspend/sleep/hibernate targets masked. Debian lightdm/XFCE running. Network reachability and jobs are verified without a reboot, but unattended reboot and closed-lid acceptance are separate required tests.

## Test and operational notes
The full python3 scripts/validate.py suite passed 1,055 Python tests, JavaScript and Compose checks. No new Git branch, task scheduler, controller, CRM or credentials in Git. Previously uncommitted linux-worker/repair-boot-mounts.sh was deliberately left untouched.

Review on the Razer:
- tailscale ssh root@100.77.117.120 'nmcli -t -f DEVICE,STATE,CONNECTION device status; ip route get 1.1.1.1'
- tailscale ssh root@100.77.117.120 'systemctl is-active oci-samsung-wifi.timer open-ci-edge-worker oci-raeburnai dev-zram0.swap'
- python3 app_runtime.py edge-list
- python3 fleet_connectivity.py status

Acceptance still needed: controlled reboot, Wi-Fi and Tailscale SSH reconnect automatically without S22 USB tether, primary AI health, fresh worker heartbeat and bounded job after boot. Do not claim verified reboot until actually tested.
