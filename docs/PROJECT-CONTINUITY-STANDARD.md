# Raeburn project continuity standard (ChatGPT Chat + GitHub + Open CI Hub)

Purpose: enable a new ordinary ChatGPT conversation to pick up any project without relying on previous chat history or repeated requests.

## Existing records: use, do not duplicate

Portfolio operations and human-only decisions: [Raeburn Daily Control Centre](https://docs.google.com/spreadsheets/d/1mHlBYnRjFgwH-eMkeLCQwlqjl-gbV3hyFx51O_9tXgk/edit); update its **Handover Log**, not the Action Centre unless Martin genuinely needs to decide or act.

Repository catalogue, evidence and production-readiness: [Raeburn Repository Production Readiness Audit](https://docs.google.com/spreadsheets/d/17Y_WgAFuiKXaCOEPoWdlyyhqLHsmkV1wBfH0qnpaLTQ/edit); use **Execution Log** and **Blocked Items** without wiping historical evidence.

Project-specific implementation plans: keep each existing product's dedicated tracker. In particular, [Recruitment CRM Master Tracker](https://docs.google.com/spreadsheets/d/1XskpVsVOaq4Fzm5qwdyKbqZ2_F1ZDJcUbZIuZN8mJgA/edit) already contains its detailed workstreams.

## Every active code repository

Maintain one GitHub issue called **Project delivery — single source of truth**. Use the template at `.github/ISSUE_TEMPLATE/project-delivery.md` and link it from README or a short `docs/PROJECT-HANDOVER.md` file. Do not create duplicates if an equivalent canonical issue exists.

Each action must record:
- stable ID, precise requirement and definition of done;
- priority, responsible owner and (if genuinely known) deadline;
- status: TODO / IN PROGRESS / BLOCKED / VERIFIED DONE / NOT APPLICABLE;
- working branch or commit, test/CI job URL, output evidence, verified date;
- next permitted action and any decision that only a human can approve.

"Verified done" means the actual change has landed and acceptance checks have run successfully on the intended environment. A green GitHub-hosted placeholder does not prove local CI execution. A passing unit suite is not equivalent to verified production release.

## Session close-out contract for ordinary Chat

1. Read the current GitHub issue and relevant Drive tracker first; inspect live code and services.
2. Update the issue and any touched project's handover BEFORE finishing the chat; record exact commit and evidence.
3. Only report completed work when checkable evidence exists. Record blocked steps plainly.
4. Keep GitHub changes in a reviewed branch/PR; leave draft PRs as draft until live release gates pass.
5. For Drive, update the Handover Log and repository Execution Log after substantive verified changes, avoiding duplicate identical entries.
6. Keep approvals, login, money, legal/compliance and sensitive production changes as explicit human decisions.
7. The handover should allow: "Continue <project> using GitHub and Open CI Hub. Read its canonical issue and Drive handover, then perform the next verified step."
8. Normal Chat does not continue itself after a turn. The Open CI Hub user service may run approved CI jobs unattended, but must not claim to be an autonomous ChatGPT agent.

## Mandatory live-run safeguards

The Open CI Hub resource broker retains thermal gating and a 4 GiB memory reserve on the current 16 GiB Intel laptop. Preserve its observation of native runner Docker reservations. Never force parallelism or weaken memory limits merely to turn a red job green. No `git clean -fd`; keep existing untracked `Service_Business/` and unrelated business containers safe.

## Change/evidence logging

Open CI Hub provider watcher persists last-observed SHAs, the broker persists queued and completed jobs, and the bridge produces local job evidence and provider statuses when configured. Do not represent this as an automatic Google Sheets sync; it is not. In an interactive Chat session update the Google handover evidence after verifying a real result.

## Deployment-specific evidence

For projects with deployments, link the immutable release artifact and SHA, environment identifier, authorised deploy actor, security/migration/backup proof, pre/post health, previous version, rollback outcome and time. Do not label a production target healthy merely because a staging build passed. All production deployment must have the correct release adapter and explicit policy approval.

## Completion gate

Before closing a delivery issue or marking release GO: every required row is VERIFIED DONE or explicitly NOT APPLICABLE, with actual test and deployment proof, security review as applicable, a rollback and backup plan, no unresolved P0/P1 security blockers, and confirmation that the source branch and deployed revision match. Record the final approval owner. If not met: keep IN PROGRESS or BLOCKED.
