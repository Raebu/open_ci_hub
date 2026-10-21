# Hub-owned CI trigger and execution bridge

ci_bridge.py is an initial local bridge implementation. It is separate from the native GitHub/GitLab runner manager: provider webhooks enter a local durable queue, then the host worker checks out the event's exact SHA and runs only owner-approved argv steps inside a digest-pinned container. The container has no network, no host Docker socket, no host credentials and bounded CPU/RAM/PIDs. The host reports the result after the container exits.

## Current scope and limits

- GitHub push and eligible pull-request events; GitLab push and eligible merge-request events.
- HMAC SHA-256 verification for GitHub and constant-time shared-token verification for GitLab.
- Private host-owned repository/branch/image/step manifest; exact SHA, deduplication and durable SQLite queue.
- No arbitrary GitHub Actions or GitLab YAML interpretation. Commands are a local owner-approved manifest, not untrusted workflow text.
- No fork code by default: PR/MR source branches must be listed explicitly, and a local manifest must never grant secrets to an untrusted repository.
- Build image must be preloaded locally and pinned by digest. The container runs with network disabled; steps requiring package downloads need a separate approved dependency-preparation stage/image.
- The first version does not include polling fallback, signed job receipts, cancellation propagation, multi-PC scheduling or robust log/artifact publishing. Do not call those capabilities complete yet.
- The host checkout uses the configured host Git credential helper if needed. Never put provider tokens in the build container or manifest.

## Install on the authorised host

Use a dedicated non-root service account or the existing desktop account only if the host owner accepts that CI jobs can consume its disk and CPU. Keep the service bound to loopback or a private authenticated tunnel. Do not expose port 8766 directly to the internet.

1. Ensure Python 3.10+, Git and Docker Engine/Compose are installed.
2. Create a private state directory and an owner-authored manifest at .local/ci-bridge/manifest.json with mode 0600.
3. Set a different random secret of at least 32 characters for each repository in the service environment. Use GitHub's X-Hub-Signature-256 webhook secret and GitLab's secret token.
4. Pre-pull the exact build image and confirm its digest.
5. Run python3 ci_bridge.py init-state, then run the service and worker under the same non-root account.
6. Configure GitHub and GitLab webhooks to http://127.0.0.1:8766/webhook/github and /webhook/gitlab only if they are delivered through an authenticated local relay/reverse tunnel. Direct provider webhooks cannot reach loopback unaided. Do not publish an unauthenticated relay.
7. After testing on a disposable repository, integrate the service with the Open CI Hub dashboard and install managed systemd user units.

## Manifest example

Replace the example repository, secret environment variable and image digest with values reviewed on the host. Never commit the real file or secrets.

~~~json
{
  "schema": 1,
  "repositories": {
    "Raebu/open_ci_hub": {
      "provider": "github",
      "secret_env": "OPEN_CI_GITHUB_WEBHOOK_SECRET",
      "branches": ["main"],
      "image": "python:3.12-slim@sha256:REPLACE_WITH_VERIFIED_64_HEX_DIGEST",
      "steps": [
        {"name": "unit-tests", "argv": ["python", "-m", "unittest", "discover", "-s", "tests"]}
      ],
      "max_seconds": 900
    }
  }
}
~~~

The example digest is intentionally invalid until replaced. Manifest argv is executed inside the pinned container with shell=False; don't interpolate payload values into argv. Workflows that require network access, credentials, privileged Docker or deployment access need separate reviewed capabilities and must not be added to this basic build worker.

## Commands

~~~sh
python3 ci_bridge.py init-state
python3 ci_bridge.py status
python3 ci_bridge.py serve --host 127.0.0.1 --port 8766
# In a second local service process:
python3 ci_bridge.py worker-once
~~~

worker-once executes one queued job and exits. A persistent supervised worker loop is not yet provided in this initial commit. Verify provider status updates and job logs before treating a build as complete.

## Release gates still required

- Run python3 -m unittest discover -s tests -v on the actual host through Open CI Hub and inspect the transcript/receipt.
- Add the bridge to the dashboard and service installer; configure restart-on-failure and bounded queue polling.
- Implement a safe outbound-polling fallback, provider status checks for both providers, cancellation, log/artifact links and immutable execution receipts.
- Verify GitLab API endpoint compatibility and status reporting against a real test project.
- Test restart/recovery, webhook replay, duplicate events, queue exhaustion, malformed bodies, invalid secrets, untrusted PRs, and exact-SHA mismatch.
- Complete an end-to-end GitHub and GitLab run on the authorised local host before enabling production repository triggers.
