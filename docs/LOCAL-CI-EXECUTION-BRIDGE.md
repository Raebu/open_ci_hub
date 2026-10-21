# Hub-owned local CI execution bridge

**Status:** opt-in source implementation. It is not enabled on a machine merely by merging this code. Initialise, configure, install and verify it locally before relying on it.

## Execution model

1. GitHub/GitLab webhooks are verified with provider signatures/tokens, delivery IDs are deduplicated, and repository/branch policy is checked before a job is queued. The fallback poller uses the already-authenticated local \`gh\` and \`glab\` CLIs.
2. SQLite is the source of truth for job IDs, exact commit SHA, actor, trigger, attempts, queue state, cooldowns, timestamps, logs, artifacts and evidence. Interrupted jobs are recovered conservatively; retries are bounded and owner-triggered.
3. The worker checks out the exact commit, verifies the checkout SHA, reads the workflow from a trusted protected branch for pull/merge requests, and validates \`.open-ci-hub.toml\`.
4. Each step runs inside a Docker container using a locally present, explicitly approved image pinned to its image ID. The container has CPU/RAM/PID/time/output limits, a read-only root filesystem, dropped capabilities, no-new-privileges, no host Docker socket and no provider credentials. Network is disabled unless a local owner separately allows it.
5. The result is posted to the exact tested commit through the provider CLI. Logs, artifacts, resource admission, image ID, step hashes and status-reporting failures are retained locally. Provider status is evidence of a report, not independent attestation.

The bridge is deliberately single-job by default. It defers transient resource pressure instead of consuming a retry or reporting a false test failure. Default reserves are 3 GiB RAM and 20 GiB disk; step and total job timeouts are bounded.

## Requirements

- Linux desktop session running as the ordinary user; never run the bridge as root.
- Python 3.11+ (the workflow parser uses the standard-library TOML parser).
- Docker Engine accessible to the ordinary user.
- \`gh\` authenticated for GitHub repositories and/or \`glab\` authenticated for GitLab projects.
- The workflow's exact container image already pulled and reviewed locally. The bridge does not automatically pull mutable image tags.
- Target branches protected at the provider when \`require_protected_branch\` is enabled. This is the default; do not bypass protection to make a job green.

## Initial setup

Run these commands from the repository directory on the authorised PC, as the desktop user, never with \`sudo\`.

1. Initialise private local state. The generated owner token is displayed once; store it securely.

   \`\`\`bash
   python3 ci_execution_bridge.py init --confirm
   \`\`\`

2. Pull and inspect the image you intend to approve. This is an owner action and may download several hundred megabytes.

   \`\`\`bash
   docker pull python:3.12-slim
   docker image inspect python:3.12-slim --format '{{.Id}}'
   \`\`\`

3. Explicitly allow each trusted repository and branch. Replace the placeholders with real names; do not use a wildcard for all branches.

   \`\`\`bash
   python3 ci_execution_bridge.py allow-repo --provider github --repo OWNER/REPO --branches main --images python:3.12-slim --confirm
   python3 ci_execution_bridge.py allow-repo --provider gitlab --repo GROUP/PROJECT --branches main --images python:3.12-slim --confirm
   \`\`\`

   Each command updates only that provider/repository entry. Pull/merge-request execution and network access remain disabled unless explicitly enabled. Fork code is rejected.

4. Add a reviewed \`.open-ci-hub.toml\` workflow to the repository. Start from [the example](../.open-ci-hub.toml.example). The workflow file is read from the protected target branch for pull/merge requests, not from untrusted request code.

5. Install and start the per-user service after configuration exists:

   \`\`\`bash
   bash scripts/install-ci-execution-bridge-service.sh
   systemctl --user status open-ci-hub-execution-bridge.service
   \`\`\`

6. Verify the queue and provider access:

   \`\`\`bash
   python3 ci_execution_bridge.py status --json
   python3 ci_execution_bridge.py poll --once
   \`\`\`

   Inspect the dashboard at \`http://127.0.0.1:8766/\`. Enter the locally generated owner token. A passing provider status is not enough: verify the job ID, exact SHA, logs, step evidence and artifact digest.

## Webhooks versus polling

The server binds only to loopback. GitHub/GitLab cannot reach that address directly. Polling is the safe default and needs no inbound port. Direct webhooks require a separately configured, authenticated HTTPS/private reverse proxy forwarding only to loopback, with a strong secret configured using \`set-webhook-secret\`. **Never expose port 8766 directly to the internet.** The reverse proxy and DNS/tunnel must be independently reviewed before webhooks are enabled.

## Workflow format

Copy \`.open-ci-hub.toml.example\` to \`.open-ci-hub.toml\`, then adjust it to the project and the image you have approved. Supported stages are \`test\` and \`build\`; arbitrary shell deployment stages are rejected. Production release is a separate signed-release flow: it requires an owner-attached offline signature, an allowlisted release application/signer, matching source commit, integrity checks and explicit local approval. It cannot be approved by a webhook alone.

Example safe defaults:

- \`network = "none"\`
- \`memory_mb = 1024\`
- \`cpus = 1.0\`
- Explicit per-step and total timeouts
- Only the required artifact paths

A workflow may run arbitrary commands *inside its isolated container*. Containerisation is a risk reduction, not a guarantee against hostile code or kernel/container escapes. Only allow trusted repositories and protected branches. Do not enable pull-request execution for general external contributors on a personal PC.

## Operations and troubleshooting

- \`python3 ci_execution_bridge.py status --json\` — queue counts, resources, last successful runs, polling and status-report history.
- \`python3 ci_execution_bridge.py poll --once\` — run one provider polling cycle.
- \`python3 ci_execution_bridge.py retry JOB_ID --confirm\` — retry a failed/interrupted job within its limit.
- \`python3 ci_execution_bridge.py cancel JOB_ID --confirm\` — request cancellation and stop the active process group.
- \`python3 ci_execution_bridge.py attach-signature JOB_ID /path/to/release.sig --confirm\` — attach an owner-owned offline signature to an awaiting release.
- \`python3 ci_execution_bridge.py approve JOB_ID --confirm\` — approve a signed release after a signature file is present.

A missing image, CLI authentication, workflow or provider status permission must be fixed explicitly. Resource pressure is deferred with a cooldown; repeated resource deferrals do not consume retry attempts. Failed test/build steps are not automatically relabelled as passes. Rollback is only supported through the existing signed-release manager for a preconfigured application.

## Remaining release gates

Before calling this production-ready, run the full regression suite through Open CI Hub on the authorised PC, verify one GitHub and one GitLab event end to end, test status permissions and a denied branch, prove restart recovery/cancellation, review container isolation and inspect a deployment/rollback on a non-production service. This implementation has not yet been independently security-reviewed.
