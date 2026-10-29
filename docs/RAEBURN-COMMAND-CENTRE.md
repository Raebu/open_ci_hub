# RaeburnAI Command Centre — governed local production deployment

Date: 29 October 2026. Owner: Raeburn Group.

## Purpose and operating boundaries

This is the operational RaeburnAI command queue inside the existing Open CI Hub. It is not a new cloud account, agent with arbitrary shell permissions, a replacement for the legacy autonomy engine, or an unrestricted production deployment bot. It provides:

- Natural-language objective intake, deterministic domain classification and a preview before submission.
- Three modes: Observe (evidence only), Assist (plus private local AI summary), Autonomous (the same owner-preapproved bounded local actions).
- A durable mission queue, idempotent submissions, progress, explicit approvals, SHA-256 result receipts and hash-chain audit verification.
- Evidence-backed priority categories derived from aggregate operational stage counts, with no customer-level source identifiers exposed.
- Local-first model processing on the Samsung Galaxy Book Go, with RaeburnAI 2B and existing smaller Qwen fallback, no confidential prompts sent to hosted models.
- An authenticated, mobile-responsive dashboard surface at Open CI Hub → RaeburnAI.
- Recurring user-systemd execution, daily briefing and separately mounted local recovery backups.

Only these executors can run automatically: operations.snapshot (read-only canonical ledger metrics), ai.memory (owner-private metadata ingestion), models.status (no inference and no spend), and ai.brief (private Samsung inference). A mission asking to deploy, publish, send, pay, book, merge, delete or otherwise change the outside world ends in an explicit HANDOFF pending review. Approving a handoff does not execute the requested operation or prove completion.

The existing Open CI Hub scheduled business engines and legacy autonomous missions are not silently replaced or redirected. User permissions, provider approvals, idempotency boundaries and no-paid-fallback policy remain intact.

## Where it lives

- Source: raeburn_command_centre.py
- State: .local/raeburn-command/missions.sqlite3, mode 0600, parent directory 0700
- Dashboard routes: /api/raeburn/command, /api/raeburn/mission, /api/raeburn/preview, /api/raeburn/create, /api/raeburn/control
- Existing dashboard server: 127.0.0.1:8765, accessible remotely only via its existing approved Tailscale Serve configuration
- Local AI backend: raeburn_ai.py and private Samsung SSH bridge; no new public network listener

The existing authenticated dashboard enforces session or approved mobile Tailscale identity. Desktop owner and approved mobile editor roles may inspect and create these fixed-scope missions; viewers and unauthenticated sessions may not. POST requests retain same-origin and X-Open-CI-Request CSRF checks. No new user was paired or granted privileges by this change.

## Mission lifecycle and safety

1. Preview a short non-sensitive objective (8–280 characters, reject likely credentials, phone numbers and email addresses).
2. Confirm and submit with a stable idempotency key; replaying the same key cannot create duplicate work.
3. The worker atomically claims one fixed safe step at a time. Result receipts include the digest of the structured, bounded output, not merely a claimed success flag.
4. Failed work stops; there is no silent uncontrolled retry. An explicitly approved safe retry is limited to one extra attempt.
5. Interrupted work becomes needs_reconciliation and is not replayed until the owner investigates. A manual reconciliation action flags needs_review rather than inventing completion.
6. Mutating/high-risk objectives end at awaiting_approval. Marking handoff_ready records an owner decision only and explicitly returns external_action_performed=false.
7. Pause/cancel controls do not interrupt a running step, and changes are audited.

For a private client case or sensitive company document, use the approved internal workflows rather than pasting full records into a mission title. The input filter is defensive, not a substitute for a privacy/DLP review.

## Running and recovering (fish-compatible)

From the Razer in the existing repository:

    cd /mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub
    python3 raeburn_command_centre.py status
    python3 raeburn_command_centre.py preview 'Review product delivery and evidence' --mode assist
    python3 raeburn_command_centre.py create 'Review product delivery and evidence' --mode assist
    python3 raeburn_command_centre.py work
    systemctl --user list-timers --all raeburn-command-worker.timer raeburn-command-daily.timer raeburn-command-backup.timer

The production worker is installed through linux-worker/install-raeburn-command.sh. Its service runs a single step every approximately 45 seconds; it cannot execute arbitrary programs submitted by a mission. The daily briefing is queued at 08:30 Europe/London, with a once-per-day idempotency key. Existing operational and AI model monitoring schedules remain separate.

## Backup and restoration

The internal NVMe at /home is a separate mounted filesystem from the Kingston external drive holding the project. Mission snapshots are created by raeburn_command_backup.py into the owner-private directory:

    ~/.local/share/open-ci-hub/command-backups/

The backup uses the SQLite online backup API (consistent even while the WAL database is live), verifies PRAGMA integrity_check and the independent SHA-256 audit chain, writes a 0600 snapshot and a metadata manifest with a file digest, and retains 14 recent backup families. The nightly user timer is scheduled at approximately 03:45 UK time; the first real snapshot was created and verified on 29 October 2026.

Verify an existing snapshot from the owner session using the raeburn_command_backup.verify(path) Python function. Restoration is deliberately **not automatic**: stop the mission worker, independently confirm the required restore point, copy to a new temporary DB, verify the audit chain, then obtain explicit owner approval before replacing live state. A recovery rehearsal can validate a snapshot without replacing or modifying the live DB.

This backup is LOCAL only. The internal SSD remains in the same physical location and the backup is not proven encrypted at rest or replicated offsite. Independent encrypted offsite recovery is a separate requirement before making any claim of disaster-recovery-complete production readiness.

## Operational acceptance evidence

- Authenticated live dashboard returned HTTP 200; unauthenticated Command Centre API correctly returned HTTP 401.
- Authenticated owner session passed the live GET Command Centre and POST preview endpoints, reporting an intact audit chain and bounded plan.
- One actual owner-local mission (Review current Raeburn operational evidence) completed 3 of 3 scheduled fixed steps with three SHA-256 receipts and a valid audit chain.
- Six categories of outstanding evidence were surfaced from canonical operations metadata; they are unverified risk queues, not evidence that external work was completed.
- The original Qwen services, Razer dashboard, legacy autonomy code and existing memory/free AI monitors were preserved.
- Local backup to a separate filesystem passed SQLite integrity, audit-chain verification and read-only restore simulation.
- Tests cover authentication/role separation, CSRF, limits, idempotency, concurrency, crash handling, preview/confirmation, receipts, audit tamper detection, mobile UI syntax and backup integrity.

## Known boundaries requiring further acceptance

1. Production software modification/deployment, outbound commercial correspondence, real calendar changes, payments and GIBP settlement are NOT authorised through this queue. Connecting external executors requires scope-limited integration tests, approvals, rate limits and independent acceptance.
2. The RaeburnAI model learns operational evidence through immediate retrieval, but weight fine-tuning still requires human-reviewed examples and an approved training environment.
3. The S22 Ultra migration is deferred at the owner's request; Samsung Book Go is the current inference host.
4. Mobile UI is served through the existing authenticated Tailscale site; automated mobile role tests passed, but physical-browser acceptance on the owner's phone is not yet independently witnessed.
5. Full offsite encrypted recovery and disaster-recovery rehearsal are not yet proven.
6. No hosted cloud provider may be used for confidential data, and uncertain free-tier billing blocks unattended use.

The production claim for this release is specifically: a running, authenticated, recoverable, governed *local mission/control service*. It is not a claim that every Raeburn business system has unrestricted autonomous execution.
