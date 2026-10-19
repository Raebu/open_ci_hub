# GitLab group fleet — reviewed native CI migration

After sign-in to GitLab with the official glab CLI, Open CI Hub can discover projects under an authorised group (including subgroups), inspect existing GitLab CI configuration, and propose merge requests that preserve the original script commands while adding native local-ci runner tags.

The generated jobs use GitLab's own native CI scheduler and an authorised, **protected**, online project/group runner. It is not an independent CI execution engine.

## Discover without mutations

~~~fish
python3 gitlab_fleet.py scan Theraeburngroup
~~~

Only eligible group-owned, non-archived, non-fork projects are considered. A restrictive, explicit global default-branch-only CI rule is required to avoid untrusted merge-request code reaching a persistent personal machine. Pipelines containing includes, images, existing tags, job inheritance, triggers, parallelism, or ambiguous syntax are reported as manual, not modified.

## Authorise reviewed migrations

~~~fish
python3 gitlab_fleet.py authorize Theraeburngroup --confirm
python3 gitlab_fleet.py sync Theraeburngroup --apply --limit 5
~~~

An approved group permits the tool to create up to five review-only merge requests per pass. It **never merges** and never changes default branches directly. It verifies an online eligible protected native GitLab runner immediately before writes and re-checks unchanged source and the default branch.

An existing fleet MR prevents duplicate requests. Failed creation may leave an unmerged review branch. Inspect and resolve any orphaned branches safely before retrying.

## Garuda Linux hourly service

~~~fish
bash scripts/install-gitlab-fleet-service.sh
systemctl --user enable --now open-ci-hub-gitlab-fleet@Theraeburngroup.timer
journalctl --user -u open-ci-hub-gitlab-fleet@Theraeburngroup.service -n 50
~~~

This timer is installed disabled. It does not start without explicit scope authorisation and enabling. It scans every hour while the PC and user systemd instance are running. Nested subgroup names in systemd unit instances require appropriate systemd escaping; the manual CLI accepts nested group paths directly.

Stop automatic reviews:

~~~fish
systemctl --user disable --now open-ci-hub-gitlab-fleet@Theraeburngroup.timer
~~~

## Caveats

GitLab pipelines with complex configuration, unprotected references, external includes or images cannot be mechanically moved to a persistent shell executor without a security and compatibility review. Other CI systems may also be triggered by a branch or MR; inspect providers before enabling bulk scans. Native GitLab self-hosted execution avoids GitLab hosted runner minutes, but the project still needs compatible tools on the actual worker. Never assume a CI job passed without a real run URL and logs.

GitHub account/organisation support is available in docs/FLEET.md. CircleCI retains native onboarding and readiness checking but complex existing CircleCI migration has not been automated or verified end-to-end.
