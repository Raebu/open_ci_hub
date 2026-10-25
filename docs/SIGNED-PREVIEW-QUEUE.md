# Signed CI artifact → Garuda PC preview queue

**Status: review-only code, not installed, not authorised or connected to a real
GitHub/GitLab CI attestor. No live release happened.**

This adds a durable, owner-enrolled *preview-only* queue on top of
\`pc_releases.py\` from [PR #39](https://github.com/Raebu/open_ci_hub/pull/39).
The implementation is a dependency branch; do not merge until PR #39 is
reviewed and merged, or rebase it onto the final accepted release controller.

## Boundaries

- This is not a public HTTP API, a root service or a CI runner shell.
- All preview tasks require an Ed25519 **signed** manifest from an external
  trusted issuer whose public key the owner pinned locally, independently of
  GitHub or the job itself. The private signing key never resides in an
  untrusted CI runner, GitHub Issues or in this repo.
- The issuer must independently authenticate GitHub/GitLab events, inspect
  the actual immutable checked-out SHA and successful required job logs, and
  bind the *exact* output ZIP digest to the signed proof.
- **The PC verifies the signature and claims, not the remote CI logs itself.**
  A signer that merely rubber-stamps worker JSON is not a trust boundary.
  Provisioning that independent issuer is a **separate pending milestone**.
- The signed request names exactly one app, repository, branch prefix,
  source commit, checked-out SHA, artifact digest, CI run ID, required observed
  checks, validity window and one-time nonce.
- Only \`environment=preview\` is accepted. No production access, root, arbitrary
  shell, Docker socket, external deploy key or change to existing app services.
- The normal user must have already enrolled an **isolated** managed preview
  service with a distinct loopback \`/health\` port. Feature code is executable,
  so untrusted/fork inputs must not share credentials or volumes with the host.
- Task journal is private and durable. A signed request can only be queued
  once, and a task marked running before deployment won't be blindly retried
  after a crash. A stalled task is an explicit failure requiring review.
- Verify artifact ZIP SHA-256 before queueing and again during staging; refuse
  mismatches. \`pc_releases\` validates archive paths and switches only managed
  releases. Failed health checks automatically restore the previous pointer
  and attempt to restart the previously running service.

## One-time owner steps (NOT performed by these source commits)

1. Complete security review and live proof of PRs #38 and #39; verify runner
   isolation and a disposable preview app, not an existing production service.
2. Deploy only reviewed controller and queue code to the actual PC.
3. Provision a **separate authenticated CI attestor** able to verify real job
   checks and artifact provenance. Protect and rotate its Ed25519 signing key.
4. Enrol a disposable example app and independent preview service.
5. Obtain the attestor's authentic *public* PEM key via a trusted out-of-band
   channel, not from an issue or unsigned work request.
6. Run local owner-only enrolment (values below are placeholders, not credentials):

\`\`\`fish
python3 pc_releases.py enrol demo --service --health-port 17171 --confirm

python3 signed_preview_queue.py pin demo \
  --repository Raebu/Service_Business \
  --issuer https://YOUR-VERIFIED-ISSUER \
  --public-key /absolute/path/trusted-ed25519-public.pem \
  --ref-prefix refs/heads/feature/ \
  --required-check test \
  --required-check build \
  --confirm

# Enable only after a successful forced-health-failure rollback demonstration:
python3 signed_preview_queue.py enable-preview demo --confirm

# Local ingestion from an authenticated downloader/bridge:
python3 signed_preview_queue.py submit demo \
  --manifest /absolute/path/signed-ci-proof.json \
  --archive /absolute/path/verified-release.zip

python3 signed_preview_queue.py run-once demo
python3 signed_preview_queue.py status demo
\`\`\`

\`pin\` never automatically enables processing. A public key must be an
Ed25519 key; the private key cannot be used here.

After the full live test, optionally install the **disabled** five-minute
systemd user timer template:

\`\`\`fish
bash scripts/install-signed-preview-queue-service.sh
# Separately, owner may choose to enable:
systemctl --user enable --now open-ci-hub-preview@demo.timer
\`\`\`

The timer only drains requests previously admitted into the private queue.
It does **not** independently fetch CI artifacts from the providers, sign
claims or start the CI job. Those connectors are not yet implemented.

## Signed manifest example

The attestor signs only the canonical JSON UTF-8 \`payload\` (sorted keys,
no whitespace, no NaN). The detached \`signature\` is an unpadded base64url
Ed25519 signature. Below is a **schema illustration only**; every value is
a placeholder, not a valid signed request.

\`\`\`json
{
  "payload": {
    "schema": "open-ci-hub.signed-preview.v1",
    "issuer": "https://YOUR-VERIFIED-ISSUER",
    "app": "demo",
    "repository": "Raebu/Service_Business",
    "ref": "refs/heads/feature/example",
    "commit": "0123456789abcdef0123456789abcdef01234567",
    "checked_out_sha": "0123456789abcdef0123456789abcdef01234567",
    "artifact_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "run_id": "trusted-run-123",
    "run_status": "passed",
    "checks": {
      "test": {"exit_code": 0, "observed": true, "timed_out": false, "log_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},
      "build": {"exit_code": 0, "observed": true, "timed_out": false, "log_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"}
    },
    "issued_at": 0,
    "expires_at": 0,
    "nonce": "UNIQUE_URLSAFE_NONCE_AT_LEAST_22_CHARS",
    "environment": "preview"
  },
  "signature": "BASE64URL_SIGNED_PAYLOAD"
}
\`\`\`

## Verified in source vs outstanding proof

| Gate | Current state |
| --- | --- |
| Strict typed receipt verification and Ed25519 proof | Proposed source + regression tests |
| One-use nonce and durable queue | Proposed source + regression tests |
| Exact artifact digest and local ZIP staging | Proposed source + regression tests |
| Owner-consented preview activation and health rollback | Proposed source + regression tests |
| Independently verified provider CI attestation/signer | **NOT built** |
| Authenticated GitHub/GitLab artifact ingestion | **NOT built** |
| User service installation, actual Garuda test | **NOT done** |
| Live preview/forced rollback of real Service Business | **NOT done** |
| Full all-repo branch/PR routing and isolated runner | **NOT done** |
| Production auto-release and bounded AI fixes | **NOT done** |

Run the unit tests without placing secrets in the repository. Security tests
depend on the Python \`cryptography\` package; missing cryptography is a
**blocking failure**, not permission to skip Ed25519 validation.

This feature will not eliminate ChatGPT service usage ceilings. Native runners
avoid hosted CI job-minute charges, but provider APIs, artifact storage, network,
power and compute remain subject to their normal limitations.
