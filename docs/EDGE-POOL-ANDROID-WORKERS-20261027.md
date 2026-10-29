# Open CI Hub: S22 + S25 bounded worker pool (27 October 2026)

The Razer remains the controller. Two approved Android Termux workers, s22-edge and s25-edge, keep their existing separately scoped credentials and private HTTPS broker. No credential changes, firmware flashing, arbitrary remote shell, browser control, file writes to phones, or cloud billing were introduced.

## What was implemented

- edge_pool.py: common scheduling across both existing mobile job queues, stale-device rejection, charging/RAM guards, least-backlogged routing, idempotent records, bounded SQLite lock retry, independent result verification and a local SHA-256 event chain.
- edge_pool_cli.py: owner-approved manual submission, batches, small Python/JSON project file checks, reporting and reconciliation.
- edge_pool_monitor.py: polls only explicitly named project files, with a per-content hash to prevent duplicate checks, and refuses symlinks, unsafe paths, obvious secrets and large inputs.
- scripts/install-edge-pool-monitor.sh: one Razer user systemd service plus one 5-minute timer. Runs as the Razer desktop account, not root; limited to 192MB of RAM and 30% CPU.
- tests/test_edge_pool.py and tests/test_edge_pool_monitor.py: fair routing, idempotency, secret/path rejection, audited result checking, zero-attempt failover, and never-replay of leased jobs.
- Linux Galaxy Book Go remains excluded from the default pool while USB boot is unreliable.

## What the phones can really do

These are the existing three operations, not new remote interpreters:

1. python.syntax: parse up to 1,500 UTF-8 bytes with Python AST, without importing or executing source code. Useful as a tiny syntax preflight.
2. json.inspect: parse up to 1,500 UTF-8 bytes and return JSON shape and canonical content digest. This is not JSON Schema validation.
3. hash.benchmark: bounded PBKDF2 workload with 1,000-50,000 iterations.

Full CI builds, npm installs, Docker, test suites, deployments, browser automation and arbitrary shell remain on the trusted Razer or other appropriately isolated CI machines. Android background execution still depends on device power-management behaviour.

## Routing and failover

The Razer selects an approved device only when the heartbeat is under 120 seconds old, the charging flag is true, enough RAM remains, the task is in the device allowlist, and concurrency permits it. Selection considers available RAM, queued and running jobs, recent failure count and recent pool assignments.

Every request has a stable idempotency key and the per-device broker job is recorded with a pool ID. The receipt shows device, attempts, underlying status, result digest and independent expected-result check.

When a device goes offline, a job can move to another approved device ONLY IF the old job has never been claimed or attempted and no lease was issued. Cancellation and insertion of the replacement are in one SQLite transaction. No cross-device replay of claimed, failed-with-attempts or stranded work. Maximum two moves.

Pool events have a local SHA-256 chain. This detects accidental log modification but is not an independently signed receipt or a guarantee against an attacker who can rewrite the database and hashes.

## Usage

Run on the Razer:

    cd /mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub
    python3 edge_pool_cli.py devices
    python3 edge_pool_cli.py check-file --file config/branch-cleanser.json --confirm --wait 120
    python3 edge_pool_cli.py check-file --file ../GIBP/gibp/compliance/rules.py --confirm
    python3 edge_pool_cli.py report
    python3 edge_pool_cli.py audit
    python3 edge_pool_cli.py reconcile

Batch jobs are a user-reviewed JSON array of objects, each with exactly task, payload and key; maximum 30. For example use task json.inspect with payload {"text":"{\"ok\":true}"} and a stable key pool-example-0001. Run:

    python3 edge_pool_cli.py batch --file /path/to/reviewed-batch.json --confirm

Never send genuine credentials, candidate records, banking data or production secrets as test payloads.

A private, git-ignored file, .local/edge-pool-monitor.json, lists four specific tiny files in Open CI Hub, GIBP and RaeburnEmailer. No general scan of company repositories or secrets occurs. The installed timer checks for content changes every five minutes and reconciles safely.

    systemctl --user status open-ci-edge-pool.timer
    journalctl --user -u open-ci-edge-pool.service -n 30 --no-pager
    systemctl --user disable --now open-ci-edge-pool.timer

## S22 Ultra and native Linux

As of 27 October 2026 the Galaxy S22 Ultra does not appear in the official Ubuntu Touch supported-device index. Native Linux ports must be checked against its exact model/chipset. Samsung says loading unofficial software can irreversibly trip the Knox Warranty Bit and disable Knox services.

The existing Termux worker is already a Linux-style command environment inside Android and needs no bootloader unlock. An optional Termux proot-distro Debian/Ubuntu userland would not replace the kernel, enable Docker, provide unconfined hardware access, or bypass Android background-process limitations. It isn't necessary for the three operations in this release.

Official sources:
https://devices.ubuntu-touch.io/
https://postmarketos.org/download/
https://github.com/termux/termux-app/blob/master/README.md
https://docs.samsungknox.com/admin/knox-admin-portal/kbas/kba-360039680233/

No phone operating system or bootloader was changed.

## Production limitations

The phone pool reduces host contention for tiny, pure tasks; it does not guarantee continuous availability or materially accelerate large production builds. Next extensions should only add specifically reviewed fixed workloads, with matching changes to both phone agents and the broker, followed by real device acceptance.

## Reliability repairs — 28 October 2026

The Razer's five-minute user systemd timer remains the authoritative runner.
It now performs all four explicitly curated JSON/Python checks locally
on every tick, even if both Android agents lose their worker heartbeats.
Local checks only parse inputs; they never import or execute project code.

A verified device receipt and a passing application check are now separate.
Invalid JSON/Python can produce a correctly verified worker receipt, but
the project check must be reported as FAILED, not passed. New tests cover
this distinction, both workers being offline, single-device outages,
private status storage and refusal to follow symlinks.

Each timer run atomically stores a private status snapshot at
.local/edge-pool-status.json (0600), without project source or tokens.
Read it on the Razer with:

    python3 edge_pool_cli.py health

Status distinguishes healthy/pending/degraded/failed, with stale detection
after 15 minutes without a fresh snapshot. Offline workers show heartbeat
age. The timer returns failure for genuine parsing, integrity or audit
failures rather than treating a successful timer invocation as evidence
that an application check passed.

Importantly, the monitor records outages but cannot restart a Termux
process that Android has suspended, force-stopped or killed. A connected
Tailscale app does not prove a Termux worker is online. Verify matched
Termux and Termux:Boot builds, start Termux:Boot once, check the existing
runit service and battery optimisation exclusions, then test a real
reboot when physical access permits. Do not reset phone credentials.

The Galaxy S22 Ultra is not included in the official Ubuntu Touch
supported list for this review. Keep Android/Termux rather than
attempting an unsupported native Linux firmware flash.


### S25 owner's updated battery policy (29 October 2026)

The 27 October charging-only rule described above remains the default for
the S22 and for workers without an approved S25 power profile. The S25
is now eligible for an unplugged, signed availability report with an actual
`battery_percent` greater than 20 (21–100), subject to all the original
temperature, health, Wi-Fi and RAM checks. The Hub and the pool both
independently reject an unplugged `s25-edge` report of 20 or less.

This per-device change does NOT turn off power checks for `s22-edge`,
Samsung Linux or unrelated cloud workers. Phone-side activation of the
reviewed `edge_worker.py` and `application_client.py` with its opt-in
Termux runit profile is required before a real physical S25 can report
the new structured telemetry. See `docs/ANDROID-EDGE-WORKER.md`.
