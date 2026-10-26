# Autonomous fleet and session-reuse release record — 26 October 2026

This release extends Open CI Hub without changing the running server, issuing
credentials, enrolling an unknown machine, installing a paid provider or
turning the main desktop over to a browser agent.

## Implemented and testable

- Mission rehearsal: `python3 autonomy_cli.py rehearse examples/autonomy-browser-demo.json`.
  Produces a deterministic policy/permission/step-hash report, with no execution.
  It is **not** a real website simulation or production action proof.
- Foreground-first allocation policy: explicit recent input, meeting/call,
  thermal and RAM conditions; unknown activity pauses background work.
  It is a policy component, not yet an installed KDE input telemetry watcher.
- Free-device selection: typed device offers and deterministic rankings.
  Unknown or nonzero-cost cloud candidates are rejected. No real cloud
  billing integration or new provider has been enrolled.
- Signed restricted offline envelopes: only a small approved task set, fixed
  expiry, cryptographic identity check and duplicate prevention.
  There is no LAN/Bluetooth transport in this release.
- Job-migration reservation receipts: SQLite exclusive claims, exact-digest
  conflicts, completion hashes and explicit reconciliation after expiry.
  There is no seamless live-process migration or unattended replay.
- Local consent policy for reusable sessions: exact public website, owner
  approval, short TTL and no implied permission to click/submit.

## Required follow-on acceptance, not represented as complete

1. Autonomous private GUI: Xvfb is running, but the visual action planner
   and per-site owner approval must be connected and accepted end-to-end.
2. Human-first throttling: integrate trustworthy Wayland user-idle
   telemetry, live cgroup changes and sustained foreground stress tests.
3. QR pairing: private tailnet one-time owner-approved enrolment, hard expiry,
   device proof of possession, revocation and physical Samsung/S25 acceptance.
4. Migration: remote claim transport, checkpoint restoration and signed
   completion receipts on both devices; never retry non-idempotent actions.
5. Offline mesh: trusted local discovery, encrypted peer transport and
   reconnect synchronisation, including attack/partition testing.
6. Self-healing development: verified test failure reproduction, patch
   provenance, restricted edits, tests and owner-governed merge gates.
7. Shadow mode: a real non-production rehearsal harness for browser actions
   and file changes. The present static rehearsal only proves policy.
8. Zero-trust jobs: review the currently unprivileged bwrap + per-process
   namespaces and X11 session; same-user X11 is not a VM security boundary.
9. £0 broker: live account-specific provider cost/quota APIs and automatic
   spend circuit-breakers are not connected; cloud costs remain blocked.
10. Mission cockpit: unify cross-device work, signed evidence and live
    approvals in the authenticated mobile dashboard, then physically test.
11. **Reusable encrypted login-session vault:** implemented and tested with
    AES-256-GCM, the desktop Secret Service, strict single-site cookies,
    owner consent, TTL, read-only reuse and a private isolated Chromium
    session. A real public-site browser test with empty synthetic state
    succeeded. Existing user credentials were not collected or imported.
    Actual login reuse needs one manual site login and an unlocked desktop
    keyring. See docs/BROWSER-SESSION-VAULT.md. It is NOT automatically
    enabled in background jobs and does not support high-stakes sites until
    network and browser isolation have been independently reviewed.

## Source and cleanup policy

One changeset, one PR, full validation, merge only if safe, then delete the
temporary branch after verifying the merged result. Unrelated local worktrees
must not be discarded merely to reduce branch counts.

Any acceptance claim needs the actual device/provider ID, timestamp, exact
commit, test/log evidence and outcome. A stub, test double or simulation is
not evidence that a physical worker executed real work.
