# Read-only CI evidence and authenticated artifact intake

**Status: proposed source only; not deployed, security-audited, independently
attested or connected to a live release.**

This module, \`provider_artifact_verifier.py\`, closes the *read-only provider
verification and download* part of [issue #41](https://github.com/Raebu/open_ci_hub/issues/41).
It depends on the owner-PC release/queue components in PRs #39 and #40.

## What it checks

It runs the official authenticated provider CLI: \`gh api\` for GitHub or
\`glab api\` for GitLab. It never prints credentials, posts comments,
modifies CI provider data or writes to your live PC application directories.

For both providers, it verifies:
- Explicit repository, exact push ref, source SHA and authenticated actor
- Terminal successful pipeline/run
- Every specifically required CI job succeeded on an approved *local* runner
- Actual provider job trace/logs exist; records SHA-256 digests of those logs
- The specific successful run's artifact exists and can be downloaded
- Wrapper archive has exactly one \`release.zip\`; inner ZIP is valid and not empty
- SHA-256 of the actual inner release ZIP is calculated after download
- Only a private owner-controlled output directory can receive the resulting
  unsigned evidence and artifact; no overwrite

GitHub additionally requires a pinned workflow ID and a matching SHA-256
digest for its downloaded workflow-artifact wrapper. GitLab requires a pinned
successful artifact job and its registered runner. GitLab API does not expose
the same universal authenticated wrapper digest, so this layer calculates the
digest of bytes actually returned by the provider instead.

Provider job success does not by itself prove source code was executed as
claimed. A compromised runner can falsify its own logs. Independent security
review and **job-step execution receipts that agree with provider metadata**
are still necessary for high-trust unattended production releases.

This component does **not** verify an external attestor's signature or sign
anything. Its \`independently_attested\` property is always \`false\`. Outputs
cannot be submitted directly to \`signed_preview_queue.py\`, which requires
a valid Ed25519-signed preview manifest from a pinned issuer.

## Example read-only verification (after code review and local setup)

\`\`\`fish
# A private output directory, owned by the exact normal Linux user
mkdir -p -m 700 ~/Documents/OpenCIHubVerifiedBuilds

python3 provider_artifact_verifier.py \
  --provider github \
  --repository Raebu/Service_Business \
  --run-id VERIFIED_PROVIDER_RUN_ID \
  --sha VERIFIED_40_CHARACTER_COMMIT_SHA \
  --ref refs/heads/feature/verified-branch \
  --actor Raebu \
  --workflow-id TRUSTED_GITHUB_WORKFLOW_ID \
  --check test \
  --check build \
  --output-dir ~/Documents/OpenCIHubVerifiedBuilds
\`\`\`

For GitLab, use \`--provider gitlab\`, a trusted exact GitLab repository,
\`--artifact-job build\` and omit \`--workflow-id\`. Both commands require
the applicable official provider CLI already authenticated to the exact
repository. Every argument above is a **placeholder**, not an observed run.

## Still missing before automatic deployment

1. An attestor operating under a **separate trusted principal**, outside the
   CI runner, that independently repeats/validates provider API, actual job
   and archived execution proof before signing exactly the permitted preview
   manifest. Its private Ed25519 key must not be accessible from running
   build jobs, workspace source code or an untrusted deployment process.
2. Authenticated queue hand-off that binds the actual downloaded ZIP,
   original provider run, approvable reference and immutable artifact digest.
3. Verify against real GitHub/GitLab API responses (including pagination
   corner cases, GitLab runner scope and GitHub artifact redirect handling).
4. Real Garuda host check with a **disposable, isolated** preview user service,
   forced health-check failure and proof of restoration.
5. End-to-end review for runner-executed code and user-service isolation,
   resource caps, disk use, token rotation and any repeated/retried work.
6. Separate explicit production deployment policy and safe migration strategy;
   this work is strictly preview-only.

No background timer, automatic production rollout, live host agent or container
changes are made by this PR. A running ChatGPT session does not independently
grant a host execution channel or remove ChatGPT model usage limits.
