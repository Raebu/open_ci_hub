# Hub-owned CI trigger and execution bridge

The bridge connects authenticated GitHub/GitLab events to the existing Open CI Hub application broker. It does not create a separate single-worker queue. CI jobs are stored in the broker's shared SQLite database and claimed through its ResourceGate, so global concurrency, per-project limits, RAM headroom, native-runner reservations and thermal/power admission policy apply alongside other managed application jobs.

## Current scope

- GitHub push and same-repository pull-request events; GitLab push and same-project merge-request events.
- GitHub HMAC SHA-256 verification and constant-time GitLab shared-token verification.
- Owner-authored private manifest with repository, allowed branch, pinned image digest and argv allowlists.
- Exact 40-character commit SHA, duplicate-event idempotency and shared resource-aware scheduling.
- Multiple worker threads can claim independent jobs concurrently. The shared scheduler remains the authority: a worker sleeps when resources or policy prevent admission. Per-project concurrency is configurable; the global resource policy remains managed by Open CI Hub.
- Host-side checkout, digest-pinned container execution with network disabled, no host Docker socket or provider credentials inside the container, and bounded memory/CPU/PIDs. The broker lease is renewed while a container step runs.
- Host-side commit-status reporting after a build attempt.

## Safety boundaries and known gaps

- This does not interpret arbitrary GitHub Actions or GitLab YAML. Commands come only from the local owner manifest.
- Fork PRs and cross-project GitLab merge requests are rejected by default.
- Images must be preloaded locally and pinned by digest. Containers have no network; dependencies must already be present in the image or workspace.
- Provider webhooks cannot reach a loopback listener directly. Use an authenticated, private relay/tunnel that verifies the original provider request; never expose port 8766 to the public internet without such a relay.
- Private-repository checkout needs an approved host Git credential helper. Credentials must never be passed into build containers.
- Cancellation propagation, outbound polling fallback, immutable signed execution receipts, artifact upload/linking, and independent security review remain outstanding. The service installer and dashboard are not yet wired to the bridge.
- GitLab status API behaviour and both providers' full end-to-end results must be verified against disposable test projects before production use.

## Install on the authorised host

1. Use a non-root service account or the existing desktop account only if the host owner accepts that CI jobs consume its resources. Keep service state private.
2. Ensure Python 3.10+, Git, Docker Engine and the official gh/glab CLIs are installed and authenticated on the host.
3. Create an owner-authored manifest at .local/ci-bridge/manifest.json with mode 0600. Configure a different random secret of at least 32 characters for each repository in the service environment.
4. Preload each build image and verify its exact digest. Set ram_mb and max_parallel for each repository conservatively. The shared ResourceGate can still admit fewer jobs—or none—when RAM headroom, native runner telemetry, thermal policy or global concurrency requires it.
5. Run python3 ci_bridge.py init-state. This registers the CI projects as named apps in the shared broker and configures their task budgets. It does not start a build.
6. Run python3 ci_bridge.py serve --host 127.0.0.1 --port 8766 and, in a separately supervised process, python3 ci_bridge.py worker --workers 3. Worker count is the maximum number of worker threads, not a promise that all can be admitted simultaneously.
7. Configure the provider webhook only through an authenticated private relay that forwards the raw request body and required signature/token headers. Direct provider-to-loopback delivery will not work.

## Manifest example

Replace the example repository, environment variable and digest with host-reviewed values. Never commit the real file or secrets.

~~~json
{
  "schema": 1,
  "repositories": {
    "Raebu/open_ci_hub": {
      "provider": "github",
      "secret_env": "OPEN_CI_GITHUB_WEBHOOK_SECRET",
      "branches": ["main"],
      "image": "python:3.12-slim@sha256:REPLACE_WITH_VERIFIED_64_HEX_DIGEST",
      "ram_mb": 1024,
      "max_parallel": 2,
      "max_seconds": 900,
      "steps": [
        {"name": "unit-tests", "argv": ["python", "-m", "unittest", "discover", "-s", "tests"]}
      ]
    }
  }
}
~~~

The image digest is intentionally invalid until replaced. Each argv step runs with shell=False inside the pinned image. Do not interpolate webhook fields into commands. Network access, deployment credentials and privileged operations require separate reviewed capabilities.

## Commands

~~~sh
python3 ci_bridge.py init-state
python3 ci_bridge.py status
python3 ci_bridge.py serve --host 127.0.0.1 --port 8766
python3 ci_bridge.py worker --workers 3 --poll-seconds 5
python3 ci_bridge.py worker-once
~~~

worker-once is useful for a controlled test. The persistent worker uses several threads, but each claim is still admitted by the shared resource broker. A queued job may be delayed by actual resource headroom or policy; the mere existence of another job is not itself a blocker.

## Required verification before release

- Run python3 -m unittest discover -s tests -v on the authorised host through Open CI Hub and inspect the logs/evidence.
- Verify shared scheduler admission across projects, resource reservations, parallel claims, retries, worker restarts, heartbeat loss, queue saturation and exact-SHA mismatch.
- Verify private GitHub and GitLab checkout and status updates on disposable test repositories; validate webhook relay signatures and replay handling.
- Integrate service units, dashboard queue/log views, cancellation, artifact links and outbound polling fallback.
- Complete supply-chain/container review and independent security assessment before using personal hardware for untrusted code.

## Local cancellation, evidence and owner reset (23 October 2026)

Local CI now has authenticated owner/editor dashboard endpoints at `/api/ci/local`, `/api/ci/local/job?job=ID` and `/api/ci/local/cancel`. Running cancellation retains the worker reservation until container termination is confirmed; uncertain termination remains stranded. Queued cancellation is immediate. Receipts in the existing SQLite ledger record exact source/image, step exit codes, timestamps, artifact hashes and whether provider reporting succeeded. Logs are redacted before persistence and capped at 4 MiB; artifacts are bounded to 20 MiB and reject symlink/path escapes. A periodic workspace size monitor is a safety check, not a hard filesystem quota. Failed builds no longer retry automatically unless the owner manifest explicitly declares `retry_safe: true`.

The owner subsequently requested cancellation of all existing local builds and retention of only the two newest local runs, repeated every 60 minutes. `python3 ci_reset.py --keep 2 --confirm` implements that operation across the existing Hub bridge and legacy local CI. It briefly stops/restarts only their existing services, cancels durable Hub work, clears the legacy in-memory queue, verifies/removes named Hub CI containers, and prunes older local records/logs/workspaces. It preserves application services, native runner registrations, credentials and provider history. The two retained records are selected globally across the two local CI systems. An active hourly Codex heartbeat invokes this command; routine successful resets stay quiet. A reset can interrupt a build started less than 60 minutes earlier; this is the owner's explicit cancellation policy, not a per-build timeout.

The first reset cancelled two queued Hub jobs and one active legacy build, removed 7,204 old log files and ten obsolete workspaces, and left two legacy logs. Post-reset inspection found zero Hub or legacy queued/running jobs and all three CI services active. Detailed last-run evidence is in `.local/ci-reset/last-reset.json`.

Private Directors Lounge checkout/commit/push/remote verification and test-branch deletion succeeded through the live connector. GitLab shared runners were disabled for this integration project after a real `ci_quota_exceeded` failure was observed. An additional authentication repair supplies the CLI helper during partial-clone checkout as well as clone, because Git may lazily fetch missing objects during checkout. These operations do not prove the separate application build or staging deployment is complete.
