# Open CI Hub — autonomous multi-project CI/CD and releases

**Status:** design and *initial code* delivered on a review branch. Not yet live
across GitHub/GitLab projects; deployment and AI repair remain unimplemented.
Do not mark a release complete based on a workflow definition, superseded status,
passing unit mocks or a GitHub/GitLab UI badge alone.

## Target contract

Once an owner enrols a GitHub/GitLab namespace/repository and its project
build/test/deploy recipe:

1. A provider-native push or supported PR/MR event goes to an eligible **PC-based
   self-hosted runner** with zero hosted build jobs. Run every authorised branch,
   tag, release and merge request that has an eligible runner, not just \`main\`.
2. Build and execute the *actual project-defined* lint, tests, typecheck,
   migrations (isolated database), packaging and integration smoke checks.
3. Return failure diagnostics and exact command, runner, SHA and log receipts.
4. Optionally ask a trusted coding agent to prepare up to N changes on a
   *new repair branch*, retest that candidate, and open a PR/MR with evidence.
   Never silently patch a protected branch or claim the old SHA passed.
5. Auto-deploy to an isolated local PC preview for approved feature branches.
6. Auto-promote a verified release to approved local-PC production or an
   external deployment target only after release policy, provenance, branch/tag,
   health checks and rollback requirements have passed. A production deployment
   is a distinct action from CI success.
7. Persist queue state and idempotency, resume safe in-progress work after
   restart, and raise an exception only where legally or technically required.

## Provider routing

### GitHub
Use native Actions self-hosted routing:
\`runs-on: [self-hosted, linux, local-ci]\` for trusted repositories/refs;
\`local-ci-isolated\` for feature/PR code until a **real, disposable** isolated
worker is provisioned. GitHub Actions schedules; the job executes on your PC.

The Open CI Hub repository's own self-hosted test workflow is changed in the
proposed branch to accept \`push\` on **all branches** and \`v*\` tags, scoped to
its authenticated owner \`Raebu\`, in addition to existing restricted PR runs.
This covers **this one repository** only after merger and real runner execution.
It does not rewrite other repositories.

### GitLab
Use the provider's normal pipelines with \`tags: [local-ci]\` on protected
trusted refs and a separate, constrained isolated runner for unprotected
branches and merge requests. GitLab protected runners cannot be assumed to
accept unprotected refs. Separate eligible runner registration is necessary.
\`ci_quota_exceeded\` does not prove that a local runner was assigned. Other
hosted GitLab jobs must be migrated before the hosted-minute dependency ends.

### Review/migration at fleet scale
The existing fleet controllers deliberately allowlist default branches and
create review PRs/MRs only. Broadening to *all* refs requires additional
per-repository opt-in, runner-isolation rules, migration of existing jobs,
and end-to-end checks on each language/toolchain. A single shared on/off
switch is not a safe substitute.

## Release policy module introduced

\`release_policy.py\` accepts a local administrator-controlled policy and a
*normalised, authenticated provider event*. It emits a **decision**, not a
job, a deployment or an authorisation token. It does not authenticate the
event envelope by itself.

Each project explicitly declares:
- Exact provider/repository and trusted actors
- Branch/tag globs eligible for CI
- Exact preview and production release refs
- Preview/production enablement and \`pc\` or \`remote\` deployment target
- Whether isolated PR/MR execution has **actually** been verified
- Named required check IDs and bounded auto-fix attempts (maximum 3)

Default policy should enable CI without automatic production deployment. Source
must not grant itself access by committing a policy file. Enrolment is on the
owner's host under owner-controlled state.

Before deployment, \`ready_to_deploy\` requires an independently authenticated
receipt naming exactly the expected repository, delivery ID, checked-out SHA,
run ID and every required check's exit code and log digest. It rejects
superseded, cancelled, skipped, partial and unsuccessful jobs. **The caller must
verify the receipt issuer and underlying actual logs:** JSON with
\`verified_issuer: true\` is not itself cryptographic proof.

## Local PC deployment — desired flow (NOT yet connected)

\`\`\`text
provider push/tag
   -> native runner on PC
   -> validated exact-SHA build/test
   -> immutable artifact with digest + SBOM
   -> host-user release coordinator (not runner Docker socket)
   -> stage in isolated ~/Applications/OpenCIHub/<app>/releases/<sha>
   -> verify health / smoke / migration rollback strategy
   -> atomically promote selected app/environment
   -> monitor and revert automatically on failed health checks
   -> append signed audit evidence
\`\`\`

Run local applications with limited accounts, services, ports and storage.
Never have a CI container mount the host Docker socket, run \`sudo\`, overwrite
the user's home directory or directly mutate active production services.
Credential/material hand-off between the runner and host coordinator needs
authenticated, hash-bound messages and explicit app permissions.

A non-production feature preview MUST NOT reuse production credentials,
databases or volumes. Postgres/MySQL migration rollbacks need a compatible
reversal or preflight/backup gate, not just a symlink switch.

## Automatic fixing — desired flow (NOT yet connected)

On failed CI, preserve immutable evidence and send only redacted errors
to a coding agent (ChatGPT when authorised/available, otherwise optional
self-hosted coding model). The repair controller must:

- Bound time, attempts, tokens and PC memory/CPU; stop repeated failures.
- Create an isolated repair worktree/branch at the failing SHA, edit only the
  requested repository and verify no concurrent changes were lost.
- Request tests from Open CI Hub and distinguish original from repaired SHA.
- Publish its PR/MR and evidence; allow auto-merge only for specifically
  enrolled low-risk scopes and required independent checks.
- Avoid production releases, credential changes, money movement or external
  commitments unless an explicit separate policy authorises them.

ChatGPT usage ceilings and resets remain. A local model can continue offline
only if separately installed and scheduled within the laptop's available
resources; this does not give unlimited use of GPT-6 itself.

## Immediate release gates

- [ ] Test this PR on a real native runner; inspect exact commands and outputs
- [ ] Review and merge all-ref workflow changes after security checks
- [ ] Add local isolated runner labels/resources and live proof on unprotected
      feature branches and PRs/MRs
- [ ] Migrate project CI workflows across the **approved** GitHub/GitLab fleet
- [ ] Add required per-project build/test/migration/packaging recipes
- [ ] Implement authenticated durable event queue, retry and idempotent dispatch
- [ ] Implement host-local preview and production release deployment/rollback
- [ ] Implement bounded repair agent and full retest→PR/MR loop
- [ ] Verify branch/tag→PC execution→artifact→preview/production→health→rollback
- [ ] Independent security review before unattended production release

### Security rule

**All authorised refs should be routable, not blindly trusted.** An unknown
fork, unsigned delivery, unknown actor, unsupported runner, missing receipt,
bad commit or failed check must be blocked with a clear reason—not silently
sent to a persistent personal PC or reported green.
