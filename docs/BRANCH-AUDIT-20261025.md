# Historical Open CI Hub branches — disposition, 25 October 2026

## Decision and preservation

The 23 non-main remote branches from the October backlog were reviewed against
GitHub PR status, actual Git ancestry, code differences with current main,
active worktrees and basic security requirements. BEFORE any deletion, their
exact branch tips were saved on GitHub as annotated tags under:

    archive/legacy-20261025/<original-branch-name>

These are permanent, recoverable Git commits with their original history, not
new deployment branches or functional claims.

The live branch-cleanser dry-run found:
- 16 old branches with verifiable already-merged history: retire safely.
- Seven branches with no sufficiently reliable merge proof / distinct older
  implementations: DO NOT silently merge into current main. Preserve their
  exact histories in archive tags, then manually retire the old branch names
  under the user's explicit one-branch consolidation instruction.
- main: never retire.

## Seven unique or unverified historical branches (archived, not promoted)

| Former branch | Audit conclusion |
|---|---|
| feat/coding-agent-mcp-workspaces-20261020 | Separate host command/workspace/attestation modules, including local permission/credential surface. Requires current auth review and end-to-end checks; unsafe to silently merge |
| feat/github-approved-host-execution-20261020 | Older request attestation and GitHub host-bridge implementation. Overlaps current direct_pc_mcp and user session policy; integration/security proof missing |
| feature/disk-pressure-and-legacy-runner-enforcement | Earlier resource pressure implementation. Current main has its own newer resource and runner guards; don't overwrite these with stale modules |
| feature/parallel-project-scheduler | Old scheduler/resource_probe and dashboard routes, overlapping main's workload broker and scheduling guarantees; would require collision and stress testing |
| fix/github-runner-bootstrap-audit | Earlier GitHub runner/Compose bootstrap changes with live runner credentials and resource implications; no independent image/live runner proof for replacement |
| fix/native-runner-safety | Additional runner/routing and workflow changes; local /tmp native CI worktree exists. Preserve this worktree and original remote commit; do not overwrite its workspace or silently deploy |
| fix/persist-interactive-setup-results | Changes terminal session persistence/security and console routes; current main has evolved; live interactive session lifecycle tests required before promotion |

This decision is *not* a claim that these old modules are useless. They are
preserved in Git archive tags for future targeted recovery or reimplementation.
The old branch names can safely be retired without losing the source bytes.

## Safety boundary

Ordinary daily automation deletes ONLY verified merged, protected=false,
unreferenced, past-grace branches. It does NOT auto-retire unique or unmerged
historical code: the above one-time cleanup was specifically requested by the
owner and includes an archived recovery path. No service restarts, producer
credentials, databases, secret stores, or production deployments were changed
by the archival operation.

Archived source can be inspected or restored using Git tag names. Deleting a
remote branch after an annotated tag is verified does not delete its commits.
