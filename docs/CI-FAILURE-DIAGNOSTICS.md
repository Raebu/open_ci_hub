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


## Exact failure evidence contract — GitHub Actions parity

**Never tell ChatGPT that a build failed because of a test unless an actual
test command ran and the provider returned its output.** Distinguish a
workflow refusal, a queued/unassigned runner, a provider infrastructure
failure, and a failing command.

Open CI Hub's read-only authenticated endpoint:

```http
GET /api/ci/failure?provider=github&repo=OWNER/REPO&run=RUN_ID&logs=1
```

also accepts `provider=gitlab`, and returns structured evidence:

- `run`, `commit`, `attempt`, `run_url`, `pipeline_status`
- for each failed job: `job_id`, `job`, `runner`, `runner_assigned`,
  `failed_steps`, `state` and provider failure reason
- `log_state` = `available`, `unavailable` or `not_requested`
- `log_excerpt` = **verbatim provider transcript lines after credential
  redaction**, with preserved error text/line context and line numbers
  relative to the retrieved bounded transcript
- `exit_code_from_log` only when the exit code actually appears in a
  provider transcript (otherwise `null`)
- `excerpt_kind`, `excerpt_truncated` and `log_note` so truncated and
  unclassified tails are not mistaken for proven root causes

The backend fetches at most three failed native job logs per diagnostic
request; it returns an excerpt bounded to 6,000 characters per job. The
separate **Jobs, logs & artifacts → Load log** action retrieves up to the
last 120,000 characters of the job log. The provider remains the authoritative
source for full retained log archives and run/job history.

### ChatGPT's GitHub connector — source-of-truth lookup

Given a GitHub repository and workflow **run ID**, ChatGPT can fetch the
native workflow's job list through the connected GitHub app, inspect each
failed job's steps, and request the provider's real job log via the
GitHub workflow-job-log action. Always cross-check the run commit and
runner assignment before claiming tests executed.

If the GitHub job log returns `404`, `BlobNotFound`, an empty body, or
no steps, report **exact error unavailable**. Report the observed failing
check/job and lack of runner evidence; do not fill gaps with a plausible
failure message. If a status came from `Raeburn Local CI`, inspect that
separate status/report too: GitHub Actions log APIs only serve GitHub
Actions job logs, not private logs stored by the older `act` service.

### Native execution and delivery

Preferred path for every trusted repository:

```text
GitHub push → GitHub Actions workflow → Open CI Hub managed self-hosted runner
    → real commands and exit codes → GitHub native job/step logs + artifact
    → GitHub checks and API → ChatGPT GitHub connector → evidence-based explanation
```

The existing `.github/workflows/test.yml` in Open CI Hub proves the pattern:
actual test command, execution receipt, redacted failure-tail step and retained
receipt/log artifact. Other repositories must explicitly adopt a
self-hosted workflow and compatible evidence steps; creating a PR in Open CI
Hub alone does not migrate those repositories.

**Automatic event-to-chat delivery is a separate product integration.**
A GitHub event cannot inject an unsolicited message into an arbitrary existing
ChatGPT conversation simply because the hub generated a log. ChatGPT can
retrieve full native job evidence when a user/agent initiates an investigation;
recurring checks require a deliberately enabled automation or an authorised
connected notification workflow. Do not claim real-time automatic chat delivery
until its authenticated ingress, deduplication, prompt-injection protections,
delivery confirmation and integration test are implemented.
