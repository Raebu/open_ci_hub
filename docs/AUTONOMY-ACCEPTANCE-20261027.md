# Open CI Hub autonomy acceptance — 27 October 2026

The existing Raebu/open_ci_hub repository remains the only source of truth.
This is a production-evidence report, not a claim that all seven goals are done.

| Requirement | Proven | Still required |
|---|---|---|
| Autonomous code repair and safe merge | Change polling, CI diagnostics and tested signed releases exist | A separately sandboxed coding agent, patch validation and trusted PR-review/merge criteria; no general unattended merge authority |
| Automatic login | Real GitHub encrypted browser replay and API CLI auth verified | Generic password/TOTP broker was blocked, not deployed; human-mediated MFA/captcha/passkey prompts still need owner |
| Offline mesh | Garuda-to-S25 on private LAN SSH: signed Ed25519 pure task executed on real phone and persistent second-delivery replay rejected, 27 Oct | Physically disconnected network tests and two-phone failover not yet performed |
| Dynamic zero-cost cloud | Owned S25 works; unknown-billing/cloud expenses denied | Vercel Hobby plan detected, but billing charges API returned 404 Plan not found; hard account-specific cost/overage cap still unverified, cloud dispatch OFF |
| Independent security | Existing automated source tests and sandboxed signed receipts are inspectable | An external qualified assessor must issue an independent dated signed assessment |
| S25 reboot/stranded recovery | The existing Termux runit worker and SSH boot script at ~/.termux/boot/00-open-ci-hub passed live startup validation. Strict pure-task stranded retry code and adversarial tests added | Termux:Boot app still missing; owner must install/open it once, then approve an actual reboot/disconnection test |
| S22 Ultra/mobile end to end | S25 successfully completed all three restricted task types through Hub | S22 is not on Tailscale or enrolled; physical phone controls and two-device failover remain unverified |

## Security boundaries

The S25 may execute ONLY python.syntax, json.inspect and hash.benchmark fixed
handlers. Offline delivery uses a signed task envelope with exact sender,
recipient, expiry and digest, not an arbitrary command. A private on-device
SQLite receipt records delivery before execution; a duplicate does not
execute twice. An interrupted delivery remains reserved, never blindly
replayed. The sender's private signing key is never copied to the device,
repository, logs or messages.

Stranded Android jobs are eligible for automatic queue retry only if they
carry an idempotency key, belong to an approved Android worker, are one of
the three immutable pure tasks, have expired by over 900 seconds, and have
not reached MAX_ATTEMPTS=3. Other jobs remain stranded, requiring proof
before release. A previously issued lease token no longer authorizes a late
result. These safeguards do not prove offline two-device recovery.

Automatic code generation and arbitrary auto-merge remain disabled until
isolation, test provenance, trusted approval, and external review are proven.
The cloud allocator must continue rejecting accounts with unknown quotas,
variable pricing, no hard spending cap or unavailable billing telemetry.

## Required physical and external steps

1. Install Termux:Boot from https://f-droid.org/packages/com.termux.boot/
   using the same F-Droid signing family as the installed Termux; OPEN its
   launcher ONCE. The private boot script is already configured. Android
   reboot acceptance still requires an owner-approved restart.
2. Connect the S22 Ultra to the same Tailscale network with matching trusted
   Termux and Termux:API apps. Give it a distinct identity and secret; never
   reuse the S25 credential.
3. Obtain hard-zero cost/quota proof directly from any cloud provider before
   approving new jobs there. Vercel Hobby branding is not sufficient proof.
4. Commission an independent, external security assessor to review threat
   model, code execution, SSH/GUI confinement, auth, MFA and secret storage,
   prompt injection, release attestations, cost and disaster recovery.
   Internal scans are NOT independent certification.

No new app, daemon, cloud subscription, or permanent git branch is required
for the work completed on the current device.

## Contabo existing VPS: restricted compute acceptance, 27 Oct 2026

Existing server: `contabo-talent` (8 vCPUs, approximately 17 GiB available RAM, 128 GiB free storage). The smaller VPS is resource-constrained and excluded.

One root-owned code file at `/usr/local/lib/open-ci-hub/contabo_compute.py` on the larger VPS executes three statically approved pure tasks: `json.inspect`, `python.syntax`, and `hash.benchmark`. The exact same file is version-controlled in this repository. All three real SSH-dispatched jobs returned matching independently computed results. No new subscription, VPS, persistent service, worker credential, or Git branch was created.

Each remote invocation uses an ephemeral systemd unit with DynamicUser, private networking, protected filesystem and home directories, no privileges or capabilities, 256 MiB memory, 50 percent CPU quota, 16 task limit and 30-second deadline. No production Docker socket or root-privileged job execution is exposed to the task.

Run the acceptance directly from Garuda: `python3 contabo_compute.py --smoke`. A bounded custom task can be passed as JSON to `python3 contabo_compute.py --task json.inspect` or one of the other two approved names.

The existing Contabo host is verified as a **restricted main-branch Python CI executor**, not a general-purpose cloud build runner. A live acceptance on 27 October tested main commit `27a14fdae2061661a40c615bb125921230e23faf`: **835 Python tests passed in 24.22 seconds** inside a temporary, network-isolated systemd DynamicUser unit. Receipt: archive SHA-256 `6cf6aaf7e5a493c7422ca1c64b92b08962e800d9fd2ecd03e213887a3e4a6df3`; 320 source entries; the server verified the exact Git SHA and returned structured proof. In a separate live run, all three fixed Contabo tasks also returned locally recomputed matching results: `json.inspect`, `python.syntax`, and `hash.benchmark`. This does not prove arbitrary repositories, PR code, container builds, deployment or auto-repair.

The owner can rerun both local checks and remote pinned-main acceptance using `python3 scripts/validate.py --contabo` once the current source is committed, pushed, and clean.

The Contabo VPS is **already paid for**; using it does not introduce a new subscription, but is not economically the same thing as free computing. Contabo states that unusually high traffic can result in throttling under its fair-use policy. No independently verified account-specific zero-overage contract or metered-spend proof is connected. General untrusted CI scripts, unbounded third-party workloads, auto-provisioning and speculative paid cloud placement therefore remain disabled. <https://help.contabo.com/en/support/solutions/articles/103000271195-are-there-any-bandwith-limits-at-contabo->

## Independent security assessment handoff

An external reviewer should verify the actual live environment in addition to the unit tests: host and Contabo SSH access policies and key rotation; dynamic-user sandbox restrictions and escape paths; Chrome/Xvfb session confidentiality; broker authentication and authority boundaries; prompt injection and untrusted source execution; signed receipts and replay; local/remote test provenance; idempotent mobile recovery; access to Docker and production volumes; provider spend caps; backups and rollbacks. The assessor must document severity, reproduction, ownership, remediation and a **dated independently signed pass/fail decision**. Internal reviews do not substitute for that decision.

## Hardware and account gates still open

- S25: Termux:Boot application installation/open-once and an owner-safe real reboot test; test a genuine interrupted leased task and recovery. The latest recheck during this session could not reach the phone over Tailscale/SSH, so earlier S25 worker evidence is historical until the device reconnects.
- S22 Ultra: connect the distinct device to Tailscale, then enrol and verify its own worker token, tasks and independent mobile workflow. It is not visible yet.
- Authentication: provider-specific scoped OAuth refresh is preferred. Generic automatic password/TOTP reauthentication remains unimplemented and must not bypass CAPTCHA, hardware passkey or mandatory human MFA prompts.
- Autonomous repair and merge: no blanket unattended code-changing authority has been enabled; build isolated generated-patch review, bounded retries, required checks and audited merge receipts before limited production use.
- Zero incremental cloud: obtain provider-specific account billing/quota and enforceable no-overage guarantees before turning on unattended paid-provider dispatch.
