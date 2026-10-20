# Provider Compose runner resource admission

This feature is opt-in and operates only on already-created provider Docker Compose containers. It does not register GitHub/GitLab/CircleCI runners, create containers, pull images, recreate containers, delete Docker volumes or disclose any credentials.

## Why a separate admission gate is needed

The three Compose services (github, gitlab, circleci) have bounded Docker RAM, RAM+swap, CPU, PID and capability settings. Previously, an automatic restart policy could restart those containers even when the Open CI Hub laptop policy was paused or under critical memory/temperature pressure. The source Compose templates now use restart=no; changing YAML alone does not change the restart policy of existing host containers. The owner must inspect and migrate each existing container deliberately.

The opt-in provider_runner_control.py runs as a normal desktop user with access to Docker and requires:
- An exact provider ID (github, gitlab, circleci) approved in a private local file by the host owner with --confirm;
- A preexisting Docker Compose-labelled open-ci-hub container for exactly the provider service;
- Memory no more than 2 GiB, swap equal to memory, CPU no more than 1.5 cores, up to 256 PIDs, Linux capabilities dropped, no-new-privileges, no privileged or host-network access, and Docker restart policy no;
- An unpaused laptop policy, sufficient free RAM after app and runner reservations, and safe thermal/CPU/battery conditions through the existing native_runner_observer.require_start_capacity() gate. Disk pressure integration also applies once the companion PR #27 disk code is merged.

If any check fails, the starter does not execute docker start. It does not automatically stop running jobs, because that can interrupt live builds. The Docker daemon can still be operated directly by host administrators; this is a guarded project workflow, not a security boundary against administrators with unrestricted Docker access.

## Inspect and opt in on the physical Garuda host

After updating the code:

~~~bash
cd /mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub
git pull --ff-only origin main

# Read-only report. Any existing auto-restarting/uncapped container is flagged.
python3 provider_runner_control.py status

# Inspect the current Docker resource limits and restart policy.
docker inspect --format '{{json .HostConfig}}' open-ci-github-runner

# Only after verifying each container's existing settings and preserving volumes,
# disable Docker automatic restarts without deleting the named volumes.
# This does not add missing memory, CPU or capability caps.
docker update --restart=no open-ci-github-runner

# Approve each existing provider deliberately (illustrative: GitHub only).
python3 provider_runner_control.py approve github --confirm
python3 provider_runner_control.py start github

# Optional guarded re-admission after reboot; installed but NOT enabled.
bash scripts/install-provider-runner-control-service.sh
systemctl --user enable --now open-ci-hub-provider-runners.timer
~~~

Repeat the docker update and owner approval only for providers that are already configured, credentialled and verified with the correct memory/CPU/security caps. A container that fails any guard must be migrated by a knowledgeable host administrator. Do not automatically force-recreate a container to satisfy the policy. Back up named volumes and verify provider registration first; GitLab and CircleCI credentials must be preserved.

The timer attempts to start only host-approved, safely bounded existing containers. It cannot bootstrap a new provider or fix an unbounded legacy container.

## Remaining limitations

- The broker and provider admission check share resource telemetry, but a manual administrator launching Docker outside this controller can bypass it.
- Docker resource limits cap RAM and CPU per container, but the external CI provider decides which jobs to run, subject to runner configuration.
- Only runner start is guarded. Once running, a provider may accept multiple jobs according to provider settings and Docker limits.
- The separate per-repo GitHub personal runner pool remains managed by runner_pool.py; PR #27 covers its legacy container checks.
- Real-host cgroup, Docker volume, service restart, thermal and recovery behaviour needs testing on Garuda after installation.
