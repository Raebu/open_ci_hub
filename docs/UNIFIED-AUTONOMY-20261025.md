# Unified autonomous fleet — 25 October 2026

This source release provides owner-approved Windows/Linux/Android edge capacity,
local-only screenshot interpretation, controlled natural-language mission drafts,
Ed25519 mission receipts, and a private Xvfb display controller. It preserves
the active native CI services and never moves the user's KDE mouse.

**Not all ten ambitions are production-validated.** The S25 Android device is now enrolled and has completed all three fixed edge tasks end-to-end (2026-10-26). The Windows ARM64 device, live graphical task orchestration, real free-cloud billing connections and external security attestation remain separate release gates. A branch merge alone is not proof that a physical device has run jobs.

## Owner enrolment of two devices

On your trusted Garuda host, for the Samsung Galaxy Book Go (Windows ARM64):

    python3 app_runtime.py register samsung-edge --tasks python.syntax,json.inspect,hash.benchmark --confirm
    python3 app_runtime.py budget samsung-edge python.syntax --ram-mb 128 --confirm
    python3 app_runtime.py budget samsung-edge json.inspect --ram-mb 128 --confirm
    python3 app_runtime.py budget samsung-edge hash.benchmark --ram-mb 128 --confirm
    python3 app_runtime.py parallel samsung-edge --max-parallel 1 --confirm
    python3 app_runtime.py issue samsung-edge worker --confirm
    python3 app_runtime.py edge-enroll samsung-edge --kind windows_arm64 --confirm
    python3 app_runtime.py issue samsung-edge submit --confirm

On Garuda, for the Galaxy S25 (Android ARM64):

    python3 app_runtime.py register s25-edge --tasks python.syntax,json.inspect,hash.benchmark --confirm
    python3 app_runtime.py budget s25-edge python.syntax --ram-mb 128 --confirm
    python3 app_runtime.py budget s25-edge json.inspect --ram-mb 128 --confirm
    python3 app_runtime.py budget s25-edge hash.benchmark --ram-mb 128 --confirm
    python3 app_runtime.py parallel s25-edge --max-parallel 1 --confirm
    python3 app_runtime.py issue s25-edge worker --confirm
    python3 app_runtime.py edge-enroll s25-edge --kind android_arm64 --confirm
    python3 app_runtime.py issue s25-edge submit --confirm

Run every command only ONCE. Duplicate registration or duplicate worker
credentials are rejected rather than silently broadening device access.
The worker token is printed once and kept only in the device session. The
submitter token stays with a trusted application/owner, NOT on the worker.
Never send tokens through chat, GitHub, terminal logs or screenshots.
The two devices require independent approval in the existing Tailscale
mobile identity policy. Cloud machines are not allowed by this owner-owned
device registry.

### Samsung instructions

Install official Windows 11 ARM64 Python and Tailscale. Connect to the
private tailnet. Copy the reviewed portable_edge_worker.py and
application_client.py into one private directory. Use an interactive
PowerShell session to set OCI_EDGE_URL to the Garuda private HTTPS
Tailscale Serve hostname and securely enter OCI_EDGE_WORKER_TOKEN.
Keep the laptop plugged in; the worker requires at least 1536MiB spare RAM.

    python portable_edge_worker.py --self-test
    python portable_edge_worker.py --once
    python portable_edge_worker.py --interval 60

The Windows agent performs only Python syntax parsing, small JSON validation,
and bounded hashing, never arbitrary CI scripts, Docker, or host shell.
An official GitHub Windows ARM64 self-hosted runner is a *separate* trusted
CI capability, not automatically integrated with this edge scheduler.
This Windows agent has not yet been physically installed or tested.

### S25 instructions

Install Termux and Termux:API from matching trusted sources, and Tailscale.
Connect to the same private tailnet. In Termux:

    pkg update
    pkg install python termux-api
    termux-battery-status
    termux-wifi-connectioninfo

Copy the latest edge_worker.py and application_client.py to a private
Termux directory. Set OCI_EDGE_URL to the private Garuda HTTPS Tailscale
hostname and securely input the worker token, then:

    python edge_worker.py --dry-run
    python edge_worker.py --once
    python edge_worker.py --interval 45

The S25 guard requires Wi-Fi, charger, battery >=70%, battery temperature
<40C, GOOD battery health and at least 1536MiB available memory. The worker is
now enabled under Termux runit as `oci-edge-worker`. Termux:Boot is not installed,
so recovery after an Android reboot or force-stop remains unverified. Android can
terminate Termux at any time; never make it a critical always-on CI runner. No root,
personal file access or Android security bypass is required.

Verified 2026-10-26: `python.syntax` job `3f64c4c8cda272c0133eb648cc6ce265`,
`json.inspect` job `24d80fe757e521c8129d016f743b874b`, and `hash.benchmark` job
`f5dcfcffde9e51fb798624f3a6772cfd` each completed through the Hub in one attempt.
The full repository test suite passed 810 tests.

### Prove real execution

The S25 end-to-end job path has been verified using a separate submit token. Remaining acceptance work is to test readiness expiry after disconnect, revocation, unplugging and the stranded-lease policy before authorising unattended device execution. The App Runtime dashboard and edge-list CLI show actual last-seen status, never imaginary online flags.

## Invisible desktop and local visual AI

The private virtual desktop controller uses Xvfb display :91 with a private
Xauthority cookie and a bounded user systemd service. It is independent
of KDE. The system-wide installer was blocked by pkexec, so the verified
official signed Arch package was installed in a private, hash-pinned owner
directory without root. A separate 1280x720 authenticated display was
successfully started and verified with xdpyinfo (see
docs/XVFB-PRIVATE-DESKTOP.md). Controller commands:

    python3 virtual_desktop.py check
    python3 virtual_desktop.py start --confirm
    python3 virtual_desktop.py stop --confirm

This is a private graphical framebuffer, not a complete unbounded GUI agent.
Source tests pass; live start and graphical task orchestration remain unverified.

A new vision mission step reads exactly one JPEG produced by an earlier
approved browse step. It uses only 127.0.0.1:11434 (Ollama), the installed
qwen3.5:0.8b vision model, and a strict resource gate requiring 6144MiB
free host RAM. It cannot issue further commands based on model output.
No screenshots go to commercial cloud models.

The local language composer can draft typed 1-5-step missions using
qwen2.5:1.5b with a fixed approved website list and zero spending:

    python3 autonomy_planner.py "Capture the first page and extract its heading" --host example.com

Review that result before using autonomy_cli.py submit --confirm.
The planner cannot approve arbitrary shell commands, downloads,
messages, logins, expenditure, or further website privileges.

## Evidence integrity

The completed owner mission can produce an Ed25519 public-verifiable
receipt without exporting its private key. These commands operate in
the local private .local/autonomy state, not in Git:

    python3 autonomy_attestation.py init-key --confirm
    python3 autonomy_attestation.py sign MISSION-ID --confirm
    python3 autonomy_attestation.py verify .local/autonomy/attestations/MISSION-ID.json --public-key SAVED-PUBLIC-KEY

The receipt signs the plan SHA and verified individual step hashes. It
does not independently attest that the physical phone or Windows laptop
executed the step, and an outside security review is not yet complete.

## Real-world cost

The device registry accepts only owned, explicitly approved local devices.
Unknown or positive-cost cloud candidates are refused. Oracle/Tencent
always-free status, live account billing, quotas and shutdown enforcement
cannot be verified without provider accounts and cost APIs. This is a
deliberate fail-closed boundary, NOT a claim of guaranteed £0 cloud operation.

## Ten-feature release status

1. Background browser — actual Garuda HTTPS navigation and screenshots verified.
2. Separate GUI — signed user Xvfb installed, authenticated 1280x720 display verified; general GUI task orchestration pending.
3. Human priority — local cgroups, admission and remote 2-minute capacity checks; 30-minute foreground stress test pending.
4. API/browser/vision — local screenshot-to-Ollama binding implemented; live vision under spare RAM pending.
5. Multi-device fleet — scoped Windows/Linux/Android job dispatch implemented; physical Samsung/S25 receipts and cloud accounts pending.
6. Mission engine — existing durable DAG and constrained local planner; open-ended agent not authorised.
7. Security — Tailscale bearer scopes, single-worker app identity, browser isolation; independent penetration review pending.
8. Evidence — existing receipts, fail-closed leases and Ed25519 verification; independent third-party attestor pending.
9. Zero-cost choice — unknown-cost cloud is blocked; genuine live cloud billing integrations pending.
10. Mobile mission control — fleet inventory and safe controls added to the authenticated responsive dashboard; physical S25 acceptance tests pending.

No unauthorised paid account, cloud instance, autonomous external email,
root shell, security exception or new CI service is enabled by this merge.
