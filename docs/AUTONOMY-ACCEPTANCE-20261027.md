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
