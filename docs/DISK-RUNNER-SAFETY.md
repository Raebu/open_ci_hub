# Disk-pressure and legacy runner safeguards

This independent hardening update extends workload policy to include Linux free disk space and protects previously persisted GitHub personal-runner Docker containers.

## Disk admission
The control engine samples writable capacity at / and the current user's home. It uses the lowest available MB/percentage of those two locations:

- Less than 2048 MiB or less than 5% free: **admit no new app jobs and refuse new native CI runner starts**. Existing app and CI work is not automatically terminated solely for a low-disk condition, because doing so can corrupt data.
- Less than 4096 MiB or less than 10% free: reduce new app-job parallelism to one.
- When a sensor is unreadable the dashboard explicitly reports it as unknown rather than inventing free capacity. This measurement is not a quota, and other mounted filesystems may need separate checks.

## Existing GitHub runner pool containers
An existing stopped container must pass Docker HostConfig inspection before Docker start:

- Memory strictly bounded at up to 2GiB, memory+swap no more than RAM limit.
- CPU up to 2 cores, 256 PIDs, all Linux capabilities dropped, no-new-privileges.
- Docker restart policy no automatic restart (to avoid boot bypassing resource admission).

If any assertion fails, the start is denied and the existing container, its credentials, and its named Docker volume are preserved unchanged. A host owner must decide how to migrate a legacy container. Do not delete existing volumes as a workaround.

New personal-runner-pool containers are created with these caps and no unattended Docker restart policy; the opt-in runner-pool systemd timer remains responsible for restarting them through admission checks. The separate GitHub/GitLab/CircleCI Compose runner containers have their own resource policies; deployment or recreation requires host-level validation.

## Verification on your 16GB Intel Iris Xe Garuda PC
- Check root and home partition capacity, and whether project storage under /mnt/kingston is a separate filesystem requiring its own threshold.
- Test low-space admission with synthetic telemetry; do not actually fill the disk.
- Inspect existing runner containers before any restart; save volume backups first.
- Confirm the Open CI Hub dashboard and scheduler are on the same host and security boundary.
- This is code-only until you explicitly activate/update Open CI Hub on your PC. A passing CI test does not confirm your current Docker containers have been migrated.
