# Why Open CI Hub says a workflow is not allow-listed

This guide explains **the reason for a denied personal runner**, not a generic
GitHub connectivity failure. Runner admission remains fail-closed: the
software will not silently execute arbitrary untrusted PR content on the
ordinary Garuda desktop or change to paid cloud-hosted runner minutes.

## Two different pipelines

- **CI source repository**: build/test/deploy via a reviewed YAML workflow
  under `.github/workflows/*.yml` or `*.yaml`, targeting self-hosted
  `[self-hosted, linux, local-ci]`. Runner-pool registration requires its
  stricter *push-to-default-branch* trust policy.
- **`Raebu/open-ci-chatgpt-control`**: private ChatGPT task-control queue;
  it deliberately **does not need a GitHub Actions workflow**. Its bridge
  must validate the separately signed ChatGPT MCP authorisation and execute
  approved tasks on the host. Do not add a CI workflow to make this repo pass
  a GitHub runner-pool check.

## Open CI Hub repository diagnostic

On the dashboard open **Pipeline cockpit**, select **GitHub**, enter the
`OWNER/REPO` of the actual failing *source repository*, then choose
**Explain CI workflow rejection**.

The new read-only, authenticated endpoint
`GET /api/ci/workflow?provider=github&repo=OWNER/REPO` checks the current
repository default branch and returns:

- `workflow_missing`: no CI file at the expected GitHub Actions location;
  use Native CI onboarding to create a reviewed push-only workflow
- `trigger_not_allowlisted`: workflow uses a non-allowlisted trigger, including
  a `pull_request` event; review branch policy or isolate PR builds
- `runner_label_mismatch`: job does not explicitly target
  `[self-hosted, linux, local-ci]` (hosted runners are never silently chosen)
- `job_isolation_unverified`: reusable, container or service jobs require
  further security review
- `workflow_unreadable`: file could not be read or verified
- `workflow_tree_unverified`: incomplete or truncated GitHub API tree; do
  not confuse network/permission errors with absence of a YAML file
- `control_channel_not_ci`: ChatGPT control repository is not a CI source

The runner-pool `schedule` and `scan` CLI outputs also report named blocked
repositories and deferred starts, instead of swallowing queue/runner errors.

## Why Open CI Hub itself may not qualify for the stricter pool

`Raebu/open_ci_hub/.github/workflows/test.yml` includes pull-request builds
with explicit same-repository contributor restrictions alongside pushes to
`main`. That workflow can actually run on the registered
`open-ci-hub-garuda` native runner, but the **separate personal runner-pool
registration policy** currently rejects *any workflow* containing PR events.
This is a policy conflict, **not evidence that the runner was offline**.
Do not loosen pool admission merely to conceal that conflict; either use
the existing correctly scoped native runner, or review a safer split with
isolated PR runners and a push-only personal runner workflow.

## Feedback when a job fails

The Pipeline cockpit now fetches native failure diagnostics for up to three
most recent failed GitHub or GitLab pipelines, showing failed job/step names,
runner identity, GitLab `failure_reason`, and a link to the native job. For
the full trace, open **Jobs, logs & artifacts → Load log**, which strips common
credentials and terminal control sequences. We cannot promise every provider
will supply a detailed reason: if no failed step or job is returned, the UI
must say that explicitly instead of inventing an explanation.

The host service and dashboard need to be updated and restarted as directed
by the validated deployment procedure before these new endpoints appear.
A GitHub PR alone does **not** change the currently running dashboard.

## Remaining end-to-end test

Confirm the name of **one exact repository** showing the user's quoted
“No allow-listed CI workflow detected” message. Run the new diagnostic
against that repo's default branch, verify the identified YAML file and
trigger/labels, correct its actual CI workflow through a reviewed change,
then verify an actual native runner job and its error/evidence path. The exact
quoted wording was not found in the Open CI Hub default-branch source during
this audit, so it could originate in a different caller of the CI workflow gate.
