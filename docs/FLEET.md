# Autonomous GitHub CI fleet (opt-in)

Open CI Hub uses the native provider scheduler. The fleet discovers repositories in an authorised GitHub account or organisation and creates review-only pull requests that change eligible ubuntu-latest job routing to self-hosted Linux runner labels, preserving existing build/test commands.

It does not replace GitHub's scheduler, merge pull requests automatically, or provision private repository runners. GitHub organisation runners may be shared with authorised organisation repositories; personal-account repos generally need eligible per-repository runners. Without an eligible runner the repo is reported as blocked, never marked complete.

## Read-only discovery

~~~fish
python3 fleet.py scan Raebu
~~~

The scan lists accessible, non-archived, non-forked repositories and reports compatible and incompatible workflows without writing anything.

## Approve one account or organisation scope

~~~fish
python3 fleet.py authorize Raebu --confirm
python3 fleet.py sync Raebu --apply --limit 5
~~~

Each repository is reassessed and an online native runner is verified before writing. A new open-ci-hub/fleet-* branch is prepared, only workflow runner routing is changed, and a GitHub pull request is opened for review. No default branch writes, auto-merges or hosted workflow dispatch calls occur.

An existing open fleet pull request prevents duplicates. At most five review PRs are created in one pass. A partial failure may leave a review branch; inspect that branch before retrying.

## Hourly autonomous execution: Garuda Linux

~~~fish
bash scripts/install-fleet-service.sh
systemctl --user enable --now open-ci-hub-fleet@Raebu.timer
systemctl --user list-timers 'open-ci-hub-fleet*'
journalctl --user -u open-ci-hub-fleet@Raebu.service -n 50
~~~

The timer is installed disabled and requires both explicit scope approval and explicit enabling. It will look for new repositories hourly whenever the user service and PC are running. User-service linger behaviour depends on host configuration.

To disable:

~~~fish
systemctl --user disable --now open-ci-hub-fleet@Raebu.timer
~~~

## Trust, limits and safety

Only conventional Linux GitHub workflows explicitly triggered by trusted default-branch pushes are considered. Pull-request events, ambiguous event formats, matrix jobs, container/service jobs and privileged job commands are marked manual. Every workflow is inspected; one unsafe file blocks the repository.

Creating PR branches can trigger CI from existing workflow rules or outside GitHub, so inspect provider and organisation policy before enabling broad scopes. The tool does not promise zero billing if pre-existing other hosted workflows trigger. This migration does not guarantee that build toolchains and dependencies available on hosted VMs are present on the destination runner.

Review every PR for trust, secrets, permissions, execution isolation and runner capacity. Never run untrusted public PR code on a persistent personal PC. Existing GitLab and CircleCI native onboarding remains available, but their complex existing CI migration is outside this first GitHub fleet implementation.

Statuses: review-ready (compatible migration), no-workflows (use native onboarding wizard), pending-review (existing fleet PR), review-created (PR opened), manual (human compatibility review), blocked (error or missing runner), already-native (no eligible hosted Linux job found; not a universal proof all jobs are self-hosted).

Tests: python3 -m unittest discover -s tests -v. This project tests on its self-hosted GitHub Actions runner.
