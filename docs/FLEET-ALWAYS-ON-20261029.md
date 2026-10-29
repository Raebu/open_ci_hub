# Always-on Raeburn worker recovery — 29 October 2026

Verified live on the Razer; this is a deployment/acceptance record.

## Current evidence

- Razer: Open CI Hub local HTTP 200, private Tailscale Serve HTTPS 200 with direct SNI, existing awake service and five-minute fleet timer active.
- Samsung Galaxy Book Go: Tailscale IP 100.77.117.120 online and pingable. The approved samsung-linux-edge worker has an expired heartbeat. Remote SSH requires a fresh Tailscale owner security check. No SSH bypass or remote root action attempted.
- Galaxy S22 Ultra: Tailscale 100.95.64.110 online, Termux worker offline, Termux SSH 8022 not reachable.
- Galaxy S25: Tailscale 100.123.89.45 reconnected during diagnosis, Termux SSH port 8022 responds, but native Hub worker still offline. No confirmed authenticated SSH login from the Razer.
- The existing linux-worker/repair-boot-mounts.sh has separate uncommitted owner work; preserve it.

## Samsung Galaxy Book Go recovery

1. Complete the official Tailscale SSH sign-in check for root on the verified samsung-book-go peer. Do not circumvent the account-authentication requirement.
2. Inspect the real Linux service with systemctl status open-ci-edge-worker, NetworkManager, tailscaled, ssh. Examine recent journalctl lines without displaying worker credentials. Check that its Hub hostname resolves from the Samsung and TLS reaches the private Razer Serve endpoint.
3. Verify the actual effective lid policy: logind HandleLidSwitch, HandleLidSwitchExternalPower, HandleLidSwitchDocked, masked sleep/hibernate targets, and powered-on closed-lid acceptance. Guarded pre-existing linux-worker/keep-samsung-awake.sh configures these settings only if its exact host/architecture/root-volume checks pass; do not overwrite an unknown policy.
4. Restart only a verified stopped worker with no in-flight sensitive task. Confirm it is enabled for reboot and restart-on-failure, then submit and verify an actual json.inspect job receipt.
5. The Samsung currently uses the S22 via USB tether because its Qualcomm Wi-Fi firmware is incomplete. Losing the S22 or USB tether also loses Internet on the Samsung. A supported USB Ethernet adapter to the router or verified OEM Wi-Fi firmware is required for network independence; preserve Windows/boot partitions.

## Android S22 Ultra and S25 recovery

- Open Tailscale on each phone and confirm VPN Connected; allow Always-on VPN where supported and compatible with personal network preferences.
- Open Termux and check sv status oci-edge-worker. If the existing runit service is stopped, use sv up oci-edge-worker. Do not issue or copy new credentials without checking existing scoped enrolment.
- Check Termux:API battery and Wi-Fi sensors. The protected worker requires charging, battery >=40 percent, battery health GOOD, temperature <40 Celsius, available RAM and active Wi-Fi; never bypass its resource safeguards to force jobs.
- Install Termux:Boot from trusted matching F-Droid signing family and OPEN ONCE. The S25 already has a tested private script at ~/.termux/boot/00-open-ci-hub, but Termux:Boot itself and a real reboot test were outstanding. Verify S22 boot script separately and keep its distinct worker token.
- Android Settings > Apps > Background usage/Battery: allow Unrestricted or Never sleeping for Termux, Termux:Boot, Termux:API and Tailscale when available. Keep power supply and ventilation safe.
- After restart test a real new task completion on each phone and repeated heartbeat after screen-off and controlled reboot. Android can still kill apps on low battery or force-stop; there is no guaranteed perpetual background execution.

## Existing Razer monitoring and ChatGPT handoff

This work adds fleet_connectivity.py to the EXISTING open-ci-edge-pool.timer every five minutes; no new daemon or Git branch. It distinguishes VPN online but worker stopped from device/VPN offline and worker ready for all three devices. Snapshot .local/fleet-connectivity.json is private 0600, redacted and contains stable change signature, not tokens or client data.

Commands:
    cd /mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub
    python3 fleet_connectivity.py status
    systemctl --user status open-ci-edge-pool.timer
    python3 app_runtime.py edge-list

The existing COO ChatGPT task can read this snapshot through Open CI Hub to surface real degraded states. It does not grant permission to bypass authentication, reflash firmware or perform unrestricted phone access.

## Acceptance criteria

Not achieved until Samsung: SSH-authenticated service diagnosis, successful restricted job, verified closed lid and network independence; S22/S25: current worker heartbeat, genuine completed task, Termux:Boot and reboot/screen-off retention. Existing VPN connectivity is positive evidence but not proof of persistent application workloads.
