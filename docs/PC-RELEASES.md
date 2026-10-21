# Direct PC release staging and rollback

**Status:** implementation proposed for review. The controller is **not installed
or enabled** on the Garuda desktop. No user service, container, existing app,
volume or production deployment has been changed.

## Purpose

Allow the normal non-root Garuda account to stage a verified release artifact
in an isolated, newly managed app directory and switch that app's active release
atomically. A pre-existing owner-managed systemd **user** service can optionally
be restarted. On restart/health failure, the previous release pointer is restored
and the service restart is retried.

**This is not yet autonomous production CD.** The deploy command requires
local owner confirmation; the platform's verified CI status is not yet wired
into an authenticated, event-driven host deployment queue.

### Isolation

- Managed data is under
  \`~/.local/share/open-ci-hub/pc-releases/<app>/\`. Existing working trees,
  Docker volumes and systemd service files are not modified by enrolment.
- Stage accepts **only** a local regular ZIP file with an exact caller-supplied
  SHA-256 digest and 40-character source commit SHA.
- A stage can never overwrite an existing commit directory. The ZIP validator
  rejects absolute paths, traversal, hidden files, symlinks, duplicate members,
  reserved metadata, excessive sizes and huge file counts.
- Promotion and rollback hold a per-app filesystem lock and switch only the
  managed \`current\` symlink.
- The optional service name is fixed to
  \`open-ci-hub-app-<app>.service\`, running as the user. There is no root/sudo,
  arbitrary shell, Docker socket access or remote URL execution.
- Optional health checks contact only \`127.0.0.1:<port>/health\`. A successful
  2xx response alone does not prove business functionality; app-specific smoke
  tests and contract checks are still needed.

### Manual staging and promotion (after code has passed review and is installed)

\`\`\`fish
# Start with an entirely NEW, disposable test app. No real app is enrolled by this PR.
python3 pc_releases.py enrol demo --confirm

# Obtain a locally generated build ZIP, the verified expected ZIP SHA-256,
# and exact immutable commit ID from your trusted CI artifact manifest.
python3 pc_releases.py stage demo --archive /absolute/path/build.zip --commit EXACT_40_CHAR_SHA --sha256 EXACT_64_CHAR_SHA256

python3 pc_releases.py status demo

# Switch only this managed app's pointer. No service exists unless separately
# created as a user service. No actual running application is affected.
python3 pc_releases.py promote demo EXACT_40_CHAR_SHA --confirm
python3 pc_releases.py rollback demo --confirm
\`\`\`

To integrate a real user service later, enrol a **new** app with
\`--service --health-port PORT\` and separately provision the validated user
service to read \`<managed app>/current\`. The controller does not generate
or overwrite any existing service unit.

### Missing gates for autonomous release

1. Genuine self-hosted CI pass with immutable checked-out commit SHA, complete
   per-step logs and required project checks.
2. Provider event authentication, bounded durable queue and anti-replay IDs.
3. Cryptographically verified artifact provenance tying one archive digest
   to one *passed* CI run, target repository, commit and environment. The CLI
   cannot treat a user-controlled \`verified_issuer: true\` field as proof.
4. Owner-specific initial target binding and deployment delegation,
   with production and preview permission separation.
5. Per-app deployment recipes and health checks; per-project SQL migrations
   and backup/rollback semantics before production.
6. Real live PC test: verified ZIP → staged version → user-service restart →
   health check → forced failure → working prior version restored.
7. A resource/thermal/disk admission gate before starting new preview apps;
   no interference with existing local runners.
8. Security review of same-user filesystem races, archive extraction, local
   credentials, untrusted build artifacts and application execution isolation.

**Important:** A release artifact is executable code. SHA-256 verifies bytes
against an expected digest, but does not prove it is trustworthy. Do not deploy
unknown branches, PRs or untrusted artifacts on your daily-use laptop merely
because they are archived, signed or green in a GitHub UI.

## Current scope of the PR

- \`pc_releases.py\` implements opt-in enrolment, bounded archive staging,
  status, atomic promotion, optional fixed user-service restart and health
  rollback, and explicit manual rollback.
- \`tests/test_pc_releases.py\` exercises actual ZIP contents, hashes,
  traversal/symlink denials, duplicate-stage refusal and rollback behavior
  in temporary directories; these are **unit tests, not live PC deployment
  proof**.
- No automatic agent fixes, unsigned raw shell hooks, arbitrary host file
  editing, live release scheduler or server deployment is part of this PR.

Related:
- [Autonomous release policy PR #38](https://github.com/Raebu/open_ci_hub/pull/38)
- [Autonomous release architecture](AUTONOMOUS-RELEASES.md) (PR #38)
