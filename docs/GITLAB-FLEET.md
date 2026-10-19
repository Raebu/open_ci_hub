# GitLab group fleet — reviewed native CI migration

After sign-in to GitLab with the official glab CLI, Open CI Hub can discover projects under an authorised group (including subgroups), inspect existing GitLab CI configuration, and propose merge requests that preserve the original script commands while adding native local-ci runner tags.

The generated jobs use GitLab's own native CI scheduler and an authorised, **protected**, online project/group runner. It is not an independent CI execution engine.

## Discover without mutations

~~~fish
python3 gitlab_fleet.py scan the-raeburn-group
python3 gitlab_fleet.py scan the-raeburn-group1
~~~

Only eligible group-owned, non-archived, non-fork projects are considered. A restrictive, explicit global default-branch-only CI rule is required to avoid untrusted merge-request code reaching a persistent personal machine. Pipelines containing includes, images, existing tags, job inheritance, triggers, parallelism, or ambiguous syntax are reported as manual, not modified.

## Authorise reviewed migrations

~~~fish
python3 gitlab_fleet.py authorize the-raeburn-group --confirm
python3 gitlab_fleet.py sync the-raeburn-group --apply --limit 5
~~~

An approved group permits the tool to create up to five review-only merge requests per pass. It **never merges** and never changes default branches directly. It verifies an online eligible protected native GitLab runner immediately before writes and re-checks unchanged source and the default branch.

An existing fleet MR prevents duplicate requests. Failed creation may leave an unmerged review branch. Inspect and resolve any orphaned branches safely before retrying.

## Garuda Linux hourly service

~~~fish
bash scripts/install-gitlab-fleet-service.sh
systemctl --user enable --now open-ci-hub-gitlab-fleet@the-raeburn-group.timer
journalctl --user -u open-ci-hub-gitlab-fleet@the-raeburn-group.service -n 50
~~~

This timer is installed disabled. It does not start without explicit scope authorisation and enabling. It scans every hour while the PC and user systemd instance are running. Nested subgroup names in systemd unit instances require appropriate systemd escaping; the manual CLI accepts nested group paths directly.

Stop automatic reviews:

~~~fish
systemctl --user disable --now open-ci-hub-gitlab-fleet@the-raeburn-group.timer
~~~

## Caveats

GitLab pipelines with complex configuration, unprotected references, external includes or images cannot be mechanically moved to a persistent shell executor without a security and compatibility review. Other CI systems may also be triggered by a branch or MR; inspect providers before enabling bulk scans. Native GitLab self-hosted execution avoids GitLab hosted runner minutes, but the project still needs compatible tools on the actual worker. Never assume a CI job passed without a real run URL and logs.

GitHub account/organisation support is available in docs/FLEET.md. CircleCI retains native onboarding and readiness checking but complex existing CircleCI migration has not been automated or verified end-to-end.

## Personal GitLab namespace (not a group)

GitLab usernames and GitLab groups are distinct namespaces. For the account Theraeburngroup, the real accessible groups are the-raeburn-group and the-raeburn-group1. A group scan of Theraeburngroup returns 404.

Personal discovery uses the authenticated user's GitLab owned-projects endpoint and filters strictly to USERNAME/REPO projects. It refuses another user's account and subgroups.

Read-only scan:
~~~fish
glab auth status
python3 gitlab_fleet.py scan-personal Theraeburngroup
~~~

Enable approved, review-only personal migration MRs after checking the scan:
~~~fish
python3 gitlab_fleet.py authorize-personal Theraeburngroup --confirm
python3 gitlab_fleet.py sync-personal Theraeburngroup --apply --limit 5
~~~

Personal scope approval is separate from group approval. MR creation still requires eligible protected project-specific/native runner access, an unchanged source and manual review before merge. A group runner belonging to an unrelated group does not cover personal projects.

Optional hourly monitoring, disabled by default:
~~~fish
bash scripts/install-gitlab-personal-fleet-service.sh
systemctl --user enable --now open-ci-hub-gitlab-personal@Theraeburngroup.timer
~~~

Do not enable this until account sign-in, safety review and cost implications of existing other CI workflows are confirmed. It does not establish real GitLab job execution.

## Repositories without CI

The fleet now checks the complete root directory before calling a missing GitLab CI configuration `onboard-ready`. If the file exists but cannot be read, or the directory cannot be fully checked, that project is marked `blocked` and **no file is changed**.

`onboard-ready` means a safe **create-only** `.gitlab-ci.yml` native `local-ci` smoke-test merge request can be proposed. The proposal is restricted to pushes to the configured default branch, needs a protected branch and accessible online runner, and is never merged automatically.

**Smoke-only is not full project CI.** The generated job verifies that the native runner accepted a job and executes `uname`; it does not automatically run the application's unit tests, build steps, deployments, or dependency audits. Existing workflows with sophisticated Docker images, scheduled refreshes, merge-request rules, or manual jobs remain untouched and must be reviewed individually.

A missing workflow is safe to initialise, but a missing *eligible* runner must still be registered or made accessible, and an MR that has not been merged does not execute CI on the default branch. The GitLab-native runner decides execution; Open CI Hub does not dispatch those jobs through a hidden hosted backend.
