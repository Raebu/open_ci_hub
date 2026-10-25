# Owner-private GitHub delivery of signed preview releases

**Source-only draft. No GitHub control issue has been created and the receiver
has NOT been installed, enabled, authorised or run on the Garuda PC.**

\`github_signed_preview_delivery.py\` bridges the remaining physical handoff:

\`\`\`text
Separate trusted provider verifier + signer (PRs #42–43)
    -> signed preview manifest (signature + exact SHA/artifact hash)
    -> private GitHub Issues control repository (transport only)
    -> PC owner-enrolled read-only receiver
    -> fetch exact matching GitHub run artifact by authenticated GH CLI
    -> recheck signed Ed25519 claims / artifact wrapper ZIP digest / inner ZIP hash
    -> durable local queue (PR #40)
    -> app staging & health rollback (PR #39)
\`\`\`

GitHub issues do **not** authenticate ChatGPT messages or authorise shell
commands. The receiver ignores unsigned, forged, stale, out-of-scope,
replayed or changed messages. Its local owner must independently enrol the
exact private GitHub control repository name **and stable numeric repository
ID**; unexpected transfer or public visibility blocks ingestion.

No signing key or credential is placed in an issue. Never post private
application logs, access tokens or customer data. GitHub Issues is only
transport for the **public signature and nonsecret manifest**.

The receiver currently supports **GitHub Actions artifacts only**. GitLab
requires a separate authenticated artifact transport. The signed manifest
must use a numeric GitHub run ID. The exact GitHub ZIP wrapper and nested
\`release.zip\` must agree with the provider's SHA256 and the issuer-signed
SHA256 respectively.

## Initial owner-only enrolment (not performed)

After the prerequisite PRs, security review and live isolated disposable app
setup, using the locally verified normal PC user:

\`\`\`fish
# Enrol an exact private control repo once (not a public repository):
python3 github_signed_preview_delivery.py enrol demo \
  --repo Raebu/open-ci-chatgpt-control \
  --trusted-author Raebu \
  --artifact-name release-bundle \
  --confirm

# Perform one read/verification/queue cycle; NO deployment occurs here:
python3 github_signed_preview_delivery.py poll-once demo

# A separate, already owner-authorised preview queue may later promote:
python3 signed_preview_queue.py run-once demo
\`\`\`

The private control repository mentioned here is an example specific to the
owner's existing context. The source code itself remains provider-neutral
and has **no hardcoded owner or repository**. The receiver refuses any control
repo not explicitly enrolled by the PC owner. Enrolment requires the existing
\`signed_preview_queue\` trust/consent configuration.

Each eligible open issue uses a title beginning
\`[open-ci-preview] \` and its **entire body** contains the existing signed
manifest JSON of the form \`{"payload": { ... }, "signature": "..."}\`.
The receiver has read-only GitHub permissions and never deletes or comments
on control issues. Journalled task nonces prevent duplicate execution.

## Gaps before genuine unattended operation

1. The trusted signing service must run as a distinct OS account not accessible
   to any CI runner, with its own constrained provider read-only credentials.
2. Provision a **separately authorised control-repository publisher** from
   the signer to create signed-manifest issues. It must never give arbitrary
   CI jobs access to the signing key.
3. The receiver and signer require a live Garuda test against the actual
   authorised private repo ID, legitimate GitHub artifact IDs/redirects and
   the user's approved repository branches.
4. Prove a disposable local preview app restarts successfully, and an injected
   health-check failure reliably restores the previous healthy release.
5. Add host resource isolation, recurring poll scheduling only after owner
   confirmation, and a separate GitLab artifact handoff.
6. Do not allow unattended production deployment without a distinct explicit
   production promotion policy, migration/backup safety and independent review.

The implementation does not grant continued ChatGPT inference without product
usage limits. Native local runners reduce reliance on hosted CI build minutes,
but are subject to hardware and provider account constraints.
