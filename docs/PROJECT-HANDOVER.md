# Open CI Hub — current project handover

As of 21 October 2026. This is a live-verified snapshot; re-check all system claims on each new Chat turn.

Project: [Raebu/open_ci_hub](https://github.com/Raebu/open_ci_hub)
Work branch: feat/hub-owned-ci-execution-bridge-20261021
Last documented baseline commit before deployment work (verify newer head): `c8c6853780440ee91addfdc6e8d85b51d30a770b`
Canonical tasks: [GitHub issue #48](https://github.com/Raebu/open_ci_hub/issues/48)
Review: [draft PR #47](https://github.com/Raebu/open_ci_hub/pull/47) (do not merge until release gates pass)
Host: Garuda Linux 16 GB Intel laptop, local repo under `/mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub`.

## Verified completed
- Secure ChatGPT Open CI Hub tunnel connected with all seven local capabilities enabled; terminal and file read returned live data.
- Direct PC file read output corrected to `file_text`; actual connector read worked after service reload.
- Installed new outbound provider poller and two-thread resource-aware CI worker as enabled user services.
- GitHub + private GitLab authenticated CLI branch lookup succeeded.
- First GitHub `main` observation persisted as baseline without triggering a historical build.
- Private manifest: one reviewed GitHub pilot, `main` only, Python 3.12 digest-pinned smoke tests; no automatic deployment.
- On 21 October full `scripts/validate.py` passed 537 tests, dashboard JavaScript syntax and Docker Compose checks.
- Existing GitHub/GitLab self-hosted runner containers, old one-build legacy CI and business app containers preserved.

## Deployment engine increment — 21 October 2026
- New \`deployment_engine.py\` and deployment unit tests; depends on existing \`release_manager.py\`, no new CI runner.
- Local staging-only policy, signed checks for build/tests/security/database/backup, one-target-at-a-time lock, audited history and local health-gated release/rollback.
- No live application deployed; remote adapters and production promotion are intentionally disabled pending separate target/environment isolation and safe migration planning.
- See [Deployment Engine](DEPLOYMENT-ENGINE.md) and [ATS deployment profile](ATS-DEPLOYMENT-PROFILE.md). Track P0/P1 acceptance gates in [deployment issue #49](https://github.com/Raebu/open_ci_hub/issues/49), linked to canonical issue #48.

## Deployment provider and dashboard increment
- Verified ATS GitLab production inventory: seven named container units and separate Careers/Admin frontends; existing signed/requested release interface and migration/backup/synthetic candidate checks documented.\n- Implemented credential store, Cloudflare Worker version promotion, Vercel ready-deployment alias assignment, and an allowlisted SSH/Compose staging adapter (all default OFF without owner-provisioned policy and secrets).
- Added read-only authenticated Deployments dashboard. All provider operations are unit-tested with mocks; no live provider/SSH deployment tested, and no target policy or token was installed on the host.
- Automatic production remains deliberately blocked until independent CI execution attestation, approval, environment isolation and real rollback proof. See [issue #49](https://github.com/Raebu/open_ci_hub/issues/49).

## Controlled CI internet increment — 21 October 2026

- Owner-approved Full public HTTP/HTTPS access (80/443) for protected push builds, with default Offline for all others. The Docker network remains disabled and job code accesses only a filtered host Unix-socket gateway.
- Live bounded container smoke test succeeded for public npm HTTPS; loopback, laptop LAN, RFC1918, metadata, and direct public sockets all blocked.
- Private network settings appear in the authenticated dashboard. See [CI-NETWORK](CI-NETWORK.md).
- Full here does NOT mean arbitrary TCP/UDP ports or private network access; CI build provider status and release gates remain separate.

## NOT yet complete or verified
- Changed SHA → durable enqueue → admitted Docker execution → accurate provider status, logs and receipt, proven end to end.
- GitLab project-specific review and allowlisting. Merely authenticating `glab` is not a validated Talent build.
- PR/MR webhook authentication/relay and lossless delivery; fallback polling only observes newest branch SHA.
- Restart/retry/lease-loss and cancellation drills; dashboard evidence and monitoring; independent secrets/isolation review.
- Production decision and merge of PR #47 after all release gates pass.
- Legacy CI has its own separate `CI_MAX_CONCURRENT=1` setting. New worker has at most two threads, but the resource broker may admit none.

## Resource blocker
The laptop reached 100°C during setup. A reversible power-saver profile lowered subsequent idle CPU measurements to roughly 50–60°C, but does not prove thermal health under load. Admission reserves 4096 MiB RAM for the OS and counts ~4096 MiB of existing native runner capacity. Never force admission by lowering protection. Before the next heavy run: `python3 app_runtime.py resources`, `sensors`, `free -h`.

## Next action sequence
1. Review exact current PR #47 diff and project-specific safety checks.
2. When broker explicitly admits work, use a disposable allowlisted change to prove a real end-to-end job and signed/reported commit status.
3. Verify logs, retry/rollback and a clean restart; preserve any failed evidence.
4. Prepare a meaningful GitLab project test profile with the right language/runtime and auth.
5. Update canonical GitHub delivery issue, Drive Handover Log and repository Execution Log with actual evidence.
6. Mark production GO and merge only with complete checkable evidence.

## Google Drive references
[Daily Control Centre Handover Log](https://docs.google.com/spreadsheets/d/1mHlBYnRjFgwH-eMkeLCQwlqjl-gbV3hyFx51O_9tXgk/edit)
[Repository Production Readiness Audit](https://docs.google.com/spreadsheets/d/17Y_WgAFuiKXaCOEPoWdlyyhqLHsmkV1wBfH0qnpaLTQ/edit)
[Detailed CI operating guide](STREAMLINED-AUTONOMOUS-CI.md)

Session rule: edit the canonical status issue and relevant Drive evidence, never declare success without actual receipt/proof. Ordinary Chat requires the user to initiate the next turn.
