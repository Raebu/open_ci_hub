# Independently verified preview-signing coordinator

**Review-only source. No key, service, signing account, queue, trusted
GitHub/GitLab credential or deployment has been provisioned.**

This is the signer portion of P0 issue #41 and depends on:
- PR #39 — non-root PC release controller
- PR #40 — owner-pinned signed preview queue
- PR #42 — read-only provider API and release artifact verifier

## Trust boundary

A provider CI runner must **never** hold or see the Ed25519 private key.
\`independent_ci_attestor.py\` must execute as a **different ordinary Linux
OS user** with no access from the build runners. A private signer policy
restricts that user to one specific provider, repository, source actor,
preview-branch prefix, explicit required CI jobs and trusted workflow or
artifact job. The signer itself queries GitHub/GitLab afresh, inspects the
run/job status, runner and exact source SHA, downloads the artifact and signs
its SHA-256. It never signs runner-supplied arbitrary JSON.

Required runtime isolation:
- Dedicated signer UID distinct from every CI runner UID, with a private
  \`0700\` home/config folder. Its Ed25519 key and policy are \`0600\` files.
- Exact trusted official \`gh\` / \`glab\` read-only authentication; do not
  give this user broad repository write permissions.
- Do not mount signer home/key into CI containers, app containers or Tailscale
  dashboard. Do not add signer UID to Docker group.
- Do not run signer inside a CI job, as root, or under a user whose home or
  credentials are readable by runners. The source rejects known CI environment
  variables and mismatched owner UID, but these checks are *defence in depth*,
  not a substitute for real OS isolation.
- Independent hardening and source review required before unattended use.

### Locally enrolled policy schema

A prospective policy file (placeholders, not production config) is:

\`\`\`json
{
  "schema": "open-ci-hub.attestor.v1",
  "issuer": "https://YOUR-OWN-TRUSTED-ATTESTOR",
  "app": "demo",
  "repository": "Raebu/Service_Business",
  "provider": "github",
  "trusted_actor": "Raebu",
  "ref_prefixes": ["refs/heads/feature/"],
  "required_checks": ["test", "build"],
  "workflow_id": 12345,
  "artifact_job": null,
  "private_key_path": "/ABSOLUTE/PRIVATE/KEY/PATH",
  "signer_uid": 12345,
  "blocked_runner_uids": [1000]
}
\`\`\`

The values above must be verified on the actual host; none are a command to
create new users, replace existing services or grant permissions.

For GitLab, choose \`provider: "gitlab"\`, set \`workflow_id: null\`,
and pin a specific \`artifact_job\`. This is not an unrestricted fleet
trust policy. Each approved repository/preview target requires an intentional
enrolment.

### Proposed flow

\`\`\`text
Authenticated GitHub/GitLab push
       |
       v
Self-hosted native CI runner, zero signer key access
       |
       v
Provider run/job logs, exact SHA and artifact ZIP
       |
       v
Separate trusted OS principal -> provider_artifact_verifier -> Ed25519 sign
       |
       v
Private manifest + exact release.zip
       |
       v
Authenticated transport (still to build)
       |
       v
Owner-pinned signed_preview_queue on Garuda
       |
       v
Isolated test-app preview, live health check and rollback
\`\`\`

\`independent_ci_attestor.py\` creates two files in an existing private output
directory: \`signed-preview-<nonce>.json\` and a matching \`.zip\`. It
does **not** connect to the PC inbox, install agents, enable timers or start
services. An authenticated handoff/transport is still required. The signed
manifest is valid for ten minutes only and bound to one SHA/artifact/run/nonce.

### Example standalone signing invocation (NOT yet authorised)

\`\`\`fish
python3 independent_ci_attestor.py \
  --policy /ABSOLUTE/PRIVATE/attestor-policy.json \
  --run-id EXACT_VERIFIED_RUN_ID \
  --sha EXACT_40_CHARACTER_SOURCE_SHA \
  --ref refs/heads/feature/verified-example \
  --output-dir /ABSOLUTE/PRIVATE/DELIVERY_DIR
\`\`\`

No private signing key should ever be pasted into ChatGPT, committed to Git,
inserted into GitHub Actions variables, or sent as a release artifact. On the
actual laptop, review machine separation, credential ownership, case-sensitive
scope and disk reservations before any enrolment.

## Proof still outstanding

- [ ] Native self-hosted CI tests and code/security review for this module
- [ ] Independent runner-vs-signer UID and token access verification on host
- [ ] Read-only live GitHub/GitLab API verification of real run/job/log/artifact
- [ ] An authenticated delivery channel to the signed-preview queue
- [ ] Real disposable service, successful upgrade and injected-health-failure rollback
- [ ] Local revocation, key rotation and audit-log durability exercise
- [ ] Distinct production authorisation and database/volume rollback gate

**Signing a passed provider result is not mathematically independent proof
of the physical code execution.** A compromised self-hosted runner can still
fabricate its output. Stronger attestations, immutable runner images,
isolated jobs and a security audit are required before sensitive deployments.
