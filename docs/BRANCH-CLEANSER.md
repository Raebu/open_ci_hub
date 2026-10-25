# Open CI Hub branch cleanser

## Objective

Keep only one stable working branch: main. Short-lived feature branches exist for
review and disappear once merged, verified and safely archived. Archive tags
retain full commit history; they are not active development branches.

The cleanser DOES NOT autonomously merge untested changes, delete unique
unmerged work, deploy, force push rewritten history or modify existing app
source directories.

## Daily policy

1. Read GitHub API and Git's remote refs. Both must agree on the exact SHA.
2. Preserve main, protected branches, open PR heads, locally checked-out
   worktrees, and branches less than 72 hours old.
3. To qualify for unattended deletion, the exact SHA must be an ancestor of
   current main OR have a verified GitHub merged PR with matching head SHA,
   original branch name and main base.
4. Before any deletion, permanently archive the exact head in annotated tag
   archive/cleanser/<branch-name>/<SHA-prefix>; verify that the remote tag
   points to the right complete commit SHA.
5. Delete only via a remote --force-with-lease compare-and-swap using the
   observed SHA, so any concurrent branch movement blocks deletion.
6. Any missing proof, API failure, permissions problem or unexpected repository
   identity prevents deletion. No implicit approvals or arbitrary commands.

## Operations

On reviewed local main:

    python3 -m unittest tests.test_branch_cleanser -v
    python3 scripts/validate.py
    python3 branch_cleanser.py
    bash scripts/install-branch-cleanser.sh --confirm

The installer refuses root, a non-main/dirty tracked checkout, existing user
units and provider check failures. It enables a daily 03:45 owner-user timer
with a random delay, singleton lock, 256MB memory limit and 15% CPU cap.
It can operate only on the approved Raebu/open_ci_hub GitHub origin.

Manual read-only audit: python3 branch_cleanser.py
Manual approved cleanup: python3 branch_cleanser.py --apply --confirm
Stop automation: systemctl --user disable --now open-ci-hub-branch-cleanser.timer

## Development practice

Create a short-lived worktree and one task-focused PR, run real tests, then
merge and let the daily timer archive/delete the now-unused branch. Do not
make separate branches for each minute subtask or leave unused PRs open.

Historical source with different code must be assessed against modern main
and its tests. If the old code is superseded or unsafe, archive the exact
commits and document why the implementation was not promoted. Deleting a
branch name after archival is NOT the same as declaring its code production
ready. Archive tags can be fetched and cherry-picked later.

## One-time historical audit, 25 October 2026

All 23 historic branch HEADs were first archived to
archive/legacy-20261025/BRANCH_NAME with exact SHA verification. Automated
dry-run identified 16 verified merged branches and six with unverified
distinct work. The latter require manual archival/deliberate retirement,
and are not eligible for unattended deletion.

The branch cleanser itself does not resolve independent signing, authenticated
remote agent execution, cloud billing, real CI receipt verification, or
production rollout/rollback tests.
