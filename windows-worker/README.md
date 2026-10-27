# Windows Samsung worker bootstrap

**Prepared on 27 October 2026. Not yet installed or accepted on the physical Samsung PC.**

## Current scope

This bootstrap installs the existing, reviewed Windows ARM64 fixed-operation edge worker and registers a per-user Scheduled Task. It does not grant arbitrary shell execution, run arbitrary repository scripts, build Docker images, or perform browser missions. It supports only:

- `python.syntax`
- `json.inspect`
- `hash.benchmark`

The worker requires Windows ARM64, Python 3.11+, the official Tailscale client connected to the Raeburn tailnet, trusted HTTPS access to the Razer, AC power and at least 1536 MiB free physical RAM. It uses a separate worker token and refuses work when device health is stale or resources are inadequate.

## Razer endpoint already available

The existing private Tailscale Serve endpoint is:

- `https://theraeburngroup.tail8450a3.ts.net`
- Razer Tailscale IPv4: `100.72.235.100`
- `/health` returned HTTP 200 locally and returned HTTP 200 with TLS verification when the hostname was pinned to the Razer Tailscale IP. The Razer cannot currently resolve the MagicDNS hostname through its ordinary system DNS configuration. The installer therefore requires the Samsung to resolve the hostname and verify TLS; if it fails, repair Tailscale/system DNS rather than disabling TLS or exposing a public endpoint.

The Serve endpoint already proxies to the existing Open CI Hub dashboard/application API on loopback. No new public port, second scheduler or duplicate Hub service is needed.

## Before installing on Samsung

1. Install the official Tailscale Windows client from the vendor website and sign in to the same authorised Raeburn tailnet. Approve the device if prompted. Do not use Tailscale Funnel.
2. Confirm the device is the Galaxy Book Go / Windows ARM64. This bootstrap intentionally stops if the processor architecture is not ARM64; do not bypass that check.
3. Install official Python 3.11+ for Windows ARM64.
4. Obtain this reviewed repository checkout through the user's existing authenticated GitHub access. Do not put GitHub tokens into scripts or chat.
5. On the Razer, owner-enrolment for `samsung-edge` has been prepared in the local application registry with the three fixed tasks and 128 MiB per-task budgets. The worker credential has **not** been issued yet; issue it only after the Samsung appears online in the tailnet so it can be transferred privately and promptly.

## Install

From a normal PowerShell window in the repository root (not Administrator), run:

```powershell
Set-ExecutionPolicy -Scope Process RemoteSigned
.\windows-worker\install.ps1
```

The installer checks Windows ARM64, Python, Tailscale connectivity, Razer reachability and the Hub HTTPS health endpoint before accepting a token. It copies only the two reviewed Python modules and the launcher into `%LOCALAPPDATA%\OpenCIHub\Worker`, protects the directory with a per-user ACL, stores the worker token using Windows DPAPI (bound to that Windows user), runs the worker self-test, and registers a per-user Scheduled Task. It does not request administrator rights or change firewall policy.

The token prompt uses a secure PowerShell input. Never paste the token into chat, command-line arguments, screenshots, repository files or GitHub. DPAPI means the encrypted token file is intended to be decrypted only by the same Windows user on the same Windows installation.

Start / inspect / stop the worker:

```powershell
Start-ScheduledTask -TaskName OpenCIHub-RestrictedEdgeWorker
Get-ScheduledTask -TaskName OpenCIHub-RestrictedEdgeWorker
Get-ScheduledTaskInfo -TaskName OpenCIHub-RestrictedEdgeWorker
Stop-ScheduledTask -TaskName OpenCIHub-RestrictedEdgeWorker
```

It starts at that user's logon, not before login. The task is limited to the current user; do not run it elevated.

## Acceptance

The setup is not accepted until all are observed:

- Samsung appears as online in Tailscale and can ping `100.72.235.100`.
- The HTTPS `/health` request succeeds with valid TLS.
- Worker self-test passes on the physical Samsung.
- The owner issues exactly one worker credential and binds it to `samsung-edge` using `python3 app_runtime.py edge-enroll samsung-edge --kind windows_arm64 --confirm`.
- A real harmless Hub job completes once, with a matching receipt; duplicate delivery is not executed twice.
- Disconnect, stale telemetry, low RAM and unplugged AC all prevent new work.
- Scheduled Task restart and token revocation are tested.

## Full builds and browser missions are a separate release gate

The existing edge worker is deliberately not a general CI agent. The Hub currently has no verified Windows-native build/browser job adapter that provides disposable workspaces, untrusted PR isolation, bounded logs/artifacts, cancellation and exact-SHA receipts. Do not solve that by attaching a general-purpose self-hosted runner to arbitrary GitHub workflows: that would bypass the Hub's security and execution model.

The next engineering step for a genuine full Windows worker is a Hub-owned Windows agent with a per-job disposable Windows Sandbox/Hyper-V or equivalently reviewed isolation boundary, explicit project allowlists, resource admission, no persistent production secrets, and authenticated completion receipts. Browser work additionally needs a dedicated profile, origin allowlists and separate session consent. Those capabilities require real Windows hardware acceptance before being enabled.

## Rollback

Stop and unregister the scheduled task, then remove the worker directory. On the Razer, revoke the worker credential and disable the `samsung-edge` app if the device is lost or compromised. Do not remove the Tailscale installation or alter other workers as part of rollback.
