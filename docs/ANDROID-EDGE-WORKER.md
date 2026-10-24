# Android Galaxy S25 opt-in edge worker

**Status: coded for review, NOT enrolled, installed or verified on a phone.**
This uses the existing Open CI Hub private application job queue, NOT the
native GitHub/GitLab runner or a general-purpose remote terminal.

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

    python3 app_runtime.py register raeburn-edge --tasks python.syntax,json.inspect,hash.benchmark --confirm
    python3 app_runtime.py parallel raeburn-edge --max-parallel 1 --confirm
    python3 app_runtime.py issue raeburn-edge worker --confirm
    python3 app_runtime.py issue raeburn-edge submit --confirm

Never commit credentials or paste them into chat. Keep separate worker and submit
tokens; the worker token belongs only in the phone session, never in scripts or
command arguments. Broker's default 512MiB task budget is within this worker's
cap and reserves no GPU. App registration refuses duplicate app IDs.

Copy edge_worker.py and application_client.py from the reviewed trusted revision
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

Each job runs in a child process with a POSIX virtual memory hard limit matching the approved budget (maximum 512MiB),
10 seconds of CPU time, no core dumps, a 15-second wall time limit, and
credentials scrubbed from the child process environment. The phone's
subprocess has the Android sandbox permissions of Termux: that is NOT a
security boundary suitable for running untrusted code.

If the phone goes offline *after* claiming a task, the existing Hub's lease
mechanism holds it stranded on expiry until the owner confirms the original
worker is stopped. Unattended retry/cancellation is NOT yet implemented.
Termux background work can be killed by Android. Do not depend on the phone
for critical persistent hosting; no boot-time service has been configured.

## Validation through Open CI Hub

On an authorised Open CI Hub host in an isolated checkout:

    python3 -m unittest -v tests.test_edge_worker
    python3 scripts/validate.py

These local tests cannot prove Android battery behaviour or cross-device
connectivity. A real end-to-end test must use a separately issued submit token,
enqueue a harmless sample job, and verify its exact Hub job ID/result and
phone's device health before considering deployment verified.

The current Hub broker also admits jobs against the *Garuda host's* memory and thermal budget, even when the phone will execute the work. This conservatively limits offloading when the main machine is under pressure; cross-machine resource accounting is a separate unimplemented scheduler change.

Future general cloud/Windows/Linux worker scheduling needs authenticated
capability negotiation, per-device admission and separate sandboxing.
The current docs/MULTI-PC.md feature only navigates independent dashboards.
