# Native CircleCI machine runner fleet (GitHub-backed source)

After registering an actual **CircleCI machine** resource class and runner, Open CI Hub can inspect authorised GitHub source repositories for conventional .circleci/config.yml workflows and propose review-only PRs that replace hosted machine execution with a native self-hosted resource class.

The migration preserves existing job steps; it does not replace CircleCI's scheduler, register resource classes or automatically merge changes.

## Discover and approve

~~~fish
python3 circleci_fleet.py authorize Raebu --resource-class YOUR_NAMESPACE/YOUR_CLASS --confirm
python3 circleci_fleet.py scan Raebu
python3 circleci_fleet.py sync Raebu --apply --limit 5
~~~

The policy is saved privately. Before writing a PR, the code rechecks that the native CircleCI project follows the correct GitHub repository and branch, fork PR builds are disabled, an eligible online instance exists in the intended resource class, the configuration is unchanged, and no migration PR is already open.

Only extremely simple machine-executor jobs and main-only workflow filters are eligible. Configurations involving Docker images, reusable executors, schedules, reusable orbs, jobs with custom classes, matrix jobs, unfiltered branches or ambiguous policies remain manual. An unverified CircleCI runner is never presented as operational.

## Optional hourly review-only service

~~~fish
bash scripts/install-circleci-fleet-service.sh
systemctl --user enable --now open-ci-hub-circleci-fleet@Raebu.timer
journalctl --user -u open-ci-hub-circleci-fleet@Raebu.service -n 50
~~~

The timer is initially disabled. It creates review PRs only after approval of an exact GitHub account scope and CircleCI resource class. Stop it with:

~~~fish
systemctl --user disable --now open-ci-hub-circleci-fleet@Raebu.timer
~~~

**Important:** As of 19 October 2026, no real CircleCI native push build has been verified in the connected organisation. This feature is implemented and unit-tested, but end-to-end eligibility, CircleCI plan constraints, resource-class limits, instance startup, native job logs, artifact storage and cancellation still need real provider execution evidence.

Pre-existing CircleCI workflows and other providers may schedule jobs when PR branches are created; inspect provider triggers and billing before enabling unattended scans. Never execute unknown PR code on an unisolated persistent personal worker.
