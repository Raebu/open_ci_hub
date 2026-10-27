# Bounded autonomous repair and merge gate

**Status: policy module implemented and unit-tested; provider execution remains disabled until the integration gates below are verified.** This is not a claim that an autonomous coding model or production merge bot is already connected.

## Repair path

1. Accept a patch only against an immutable base SHA in a locally enrolled repository.
2. Validate the textual diff before applying it: bounded size and file count, no binary patches, deletions, renames, symlinks, submodules, secrets, CI workflow changes, authentication code or other protected paths. Paths must match owner-controlled allowlists.
3. Apply the candidate only in a disposable worktree. Preserve the original failing run, its SHA and logs; never overwrite concurrent work.
4. Run only the project's owner-enrolled test profile inside a disposable, network-isolated worker. Record the candidate SHA, exact commands, exit codes, timeout state and log digests. A test result from another SHA is not reusable.
5. Create a PR/MR containing the patch, risk classification, base/candidate SHAs and test evidence. Do not write to a protected branch.
6. Evaluate merge eligibility against provider-authenticated evidence: same-repository trusted source, low-risk path scope, verified branch protection with no admin bypass, all required checks observed for the exact candidate SHA, clean merge state and a separate trusted approval.
7. A positive decision is only eligibility. A separate provider adapter must independently authenticate API results and execute the merge, then verify the merged commit and receipt. Any missing, stale, conflicting or unverifiable evidence blocks the merge.

## Implemented in this revision

- `repair_controller.py validate-patch`: validates bounded unified diffs against explicit owner path globs and rejects sensitive or structural changes.
- `repair_controller.py merge-decision`: fail-closed decision function requiring exact-SHA check receipts, verified issuers, a separate trusted reviewer, protected-branch policy and a verified isolated test worker.
- `tests/test_repair_controller.py`: covers valid patches, protected paths, binary/deletion/rename/symlink rejection, malformed patches, exact-SHA checks, review independence and fail-closed merge decisions.

## Explicitly not enabled

- No autonomous model is installed or connected by this module; patch generation must come from an authorised coding agent.
- No automatic provider merge is executed. The decision function deliberately returns `merge_performed: false`.
- The provider adapter must fetch and verify actual PR/MR reviews, required checks, branch-protection settings, head SHA and merge state itself. A JSON field claiming that an issuer is verified is not cryptographic proof.
- The isolated disposable patch-application/test runner, authenticated evidence adapter and end-to-end create-PR → retest → review → merge receipt still need to be wired together and tested against a real enrolled repository.
- High-risk changes (authentication, secrets, workflows, deployment, migrations, finance, security controls and policy code) remain human-reviewed and never qualify for unattended merge.
- Production deployment remains a separate release decision with its own health checks and rollback.

## Browser-work placement

Use the existing private Xvfb browser for unattended browser automation where possible; it avoids taking over the interactive desktop. A separate laptop can be a useful additional worker if it is enrolled, resource-monitored and isolated. Do not attach a persistent automation worker to a personal browser profile containing general-purpose cookies, passwords or payment sessions. Prefer a dedicated browser profile, exact-site encrypted sessions, origin allowlists and a disposable worker. Browser page content is untrusted input and must never be allowed to alter repair policy or issue shell commands.

## Acceptance criteria

- [x] Patch allowlist and sensitive-path policy implemented.
- [x] Merge eligibility gate implemented, defaulting to denial on incomplete evidence.
- [x] Focused regression tests pass.
- [ ] Disposable worktree patch application and network-isolated test runner connected.
- [ ] Authenticated GitHub/GitLab evidence adapter and real PR/MR lifecycle verified.
- [ ] Restricted end-to-end auto-merge pilot for an enrolled low-risk repository.
- [ ] Independent security review before broader unattended changes.
