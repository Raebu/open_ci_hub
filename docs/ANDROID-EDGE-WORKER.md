# Android Galaxy S25 opt-in edge worker

**Status: verified on a physical Samsung Galaxy S25 on 2026-10-26.**
The phone is enrolled as `s25-edge`; all three approved tasks have completed
end-to-end through the existing private Open CI Hub application queue. This is
NOT the native GitHub/GitLab runner or a general-purpose remote terminal.

The phone runs only fixed, short, deterministic operations:
- python.syntax: Python AST parsing, without executing the source.
- json.inspect: canonical JSON validation and SHA-256 digest.
- hash.benchmark: bounded 1,000–50,000-round PBKDF2 benchmark.

This is a conservative proof of actual mobile job processing, not a way to run
large builds, Docker containers, arbitrary AI agents or untrusted PR code on
a personal phone. No access is granted to contacts, messages or phone files.

## Before enrolment

Install trusted matching-source Termux and Termux:API builds (do not mix app
signatures from F-Droid and GitHub). Install Tailscale on the Galaxy S25 and
connect it to the trusted private tailnet. Tailscale Serve on the authorised
Garuda host must expose the existing Hub application API via verified HTTPS
on a MagicDNS hostname ending in .ts.net. Never use Funnel or public HTTP.

Within Termux (not the phone's normal Android shell):

    pkg update
    pkg install python termux-api
    termux-battery-status
    termux-wifi-connectioninfo

Allow only needed Android permissions. No root, unsafe accessibility permissions,
phone-file access or disabling of Android security protections is required.

## Private Hub application registration

Only on the trusted owner Hub, after inspecting the existing app inventory:

    python3 app_runtime.py register s25-edge --tasks python.syntax,json.inspect,hash.benchmark --confirm
    python3 app_runtime.py budget s25-edge python.syntax --ram-mb 128 --confirm
    python3 app_runtime.py budget s25-edge json.inspect --ram-mb 128 --confirm
    python3 app_runtime.py budget s25-edge hash.benchmark --ram-mb 128 --confirm
    python3 app_runtime.py parallel s25-edge --max-parallel 1 --confirm
    python3 app_runtime.py issue s25-edge worker --confirm
    python3 app_runtime.py edge-enroll s25-edge --kind android_arm64 --confirm
    python3 app_runtime.py issue s25-edge submit --confirm

Never commit credentials or paste them into chat. Worker and submit tokens are separate; the worker token stays on the phone and the submit token stays on the trusted owner Hub. The active task budget is **128 MiB**, with one job at a time and no GPU reservation. The phone reports fresh available memory and battery/charging state at each claim. The Hub binds this report to the uniquely issued worker token and approved device architecture; stale or unverifiable devices cannot claim work. App registration refuses duplicate app IDs.

For a deliberate worker-token rotation, first ensure there are no queued, leased or stranded jobs. Generate/store the replacement token privately on the phone and calculate its SHA-256 there; pass only that digest to the owner Hub:

    python3 app_runtime.py edge-rebind s25-edge SHA256_OF_NEW_WORKER_TOKEN --confirm

The command validates the unique approved device and task scope, atomically revokes the old worker credential and rebinds the new hash. The device remains unavailable until it reports fresh health. Never put the token itself in command arguments, chat, logs or Git.

Copy the CURRENT edge_worker.py and application_client.py from the reviewed trusted revision
to a private Termux working directory, then inside Termux:

    read -r -p "Hub HTTPS URL: " OCI_EDGE_URL
    read -r -s -p "Hub worker token: " OCI_EDGE_WORKER_TOKEN; echo
    export OCI_EDGE_URL OCI_EDGE_WORKER_TOKEN
    python edge_worker.py --dry-run
    python edge_worker.py --once
    # After observing real Hub receipts and checking device conditions:
    python edge_worker.py --interval 45

The URL must be a valid authenticated HTTPS *.ts.net endpoint. The standard
library client validates TLS certificates and rejects arbitrary Internet hosts.

## Device and execution guards

Before claiming, and again after claiming, the worker requires:
- Termux:API battery and Wi-Fi sensors available in under five seconds.
- Battery health GOOD, charge >=70%, plugged into power.
- Battery temperature <40C. This is battery temperature, NOT a CPU sensor.
- Wi-Fi supplicant connected with a reported non-zero address.
- A job with <=512MiB reserved RAM, zero GPU and an exact built-in task name.

Each task runs in a separate Python child process. The child applies a POSIX virtual-memory hard limit matching the approved budget (maximum 512 MiB), a 10-second CPU limit and no core dumps before reading the task payload; the parent enforces a 15-second wall-clock limit and scrubs credentials from the child environment. On Android, setting limits through `preexec_fn` aborted the child, so the limit is now applied in the child entry point. Both 128 MiB and 512 MiB child tests passed on the S25. Python's interpreter bootstrap happens before the limit is applied, so this is appropriate only for the fixed, tightly bounded tasks listed above—not arbitrary or untrusted code. The subprocess retains Termux's Android permissions; this is not a general-purpose security sandbox.

If the phone goes offline *after* claiming a task, the Hub's lease mechanism holds it stranded on expiry until the owner confirms the original worker is stopped. Unattended retry/cancellation is NOT yet implemented. The worker is enabled under Termux's runit supervisor as `oci-edge-worker`; check it with `sv status oci-edge-worker`. This is supervised while Termux's service manager is running, but Termux:Boot is not installed/configured, so automatic recovery after an Android reboot or force-stop is not verified. Android can still suspend or kill Termux. Do not depend on the phone for critical persistent hosting; confirm the Hub shows the device online before relying on it.

## Validation through Open CI Hub

On an authorised Open CI Hub host in an isolated checkout:

    python3 -m unittest -v tests.test_edge_worker
    python3 scripts/validate.py

The complete repository suite passed **810 tests** on 2026-10-26. The live S25 then completed all three fixed task types through the Hub, each in one attempt:

- `python.syntax` — job `3f64c4c8cda272c0133eb648cc6ce265`, valid source, 8 AST nodes.
- `json.inspect` — job `24d80fe757e521c8129d016f743b874b`, valid JSON object and digest returned.
- `hash.benchmark` — job `f5dcfcffde9e51fb798624f3a6772cfd`, 1,000 iterations and digest returned.

The phone's Termux service log acknowledged all three IDs, and the Hub records show each as completed with one attempt. This verifies the restricted mobile task path; it does not verify arbitrary CI builds, boot-time recovery or unattended stranded-job recovery.

An explicitly owner-enrolled phone has its own server-trusted readiness
TTL and remote RAM/charging admission, so a small approved task can run
even when the Garuda host is busy. Only the three built-in fixed tasks
are eligible. A legacy unenrolled app continues using the stricter
Garuda host gate. This is not arbitrary multi-device CI script execution.

Future general cloud/Windows/Linux worker scheduling needs authenticated
capability negotiation, per-device admission and separate sandboxing.
The current docs/MULTI-PC.md feature only navigates independent dashboards.
