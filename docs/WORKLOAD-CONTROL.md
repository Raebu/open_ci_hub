# Unified Workload Control

## What changed

Open CI Hub now compares application reservations against observed native GitHub, GitLab, CircleCI and pooled GitHub Docker container memory before admitting more app jobs. It also guards provider and pool runner starts. Existing Docker Compose CI services receive 2 GiB memory, 2 GiB memory+swap, 1.5 CPUs and 256 PIDs each. External runners, unbounded child containers and independent cloud workers are not included.

The app runtime samples Linux memory PSI, relevant package thermal sensors, CPU load and laptop battery. It reduces new-job admission under moderate pressure and pauses new work during critical heat, memory pressure or low battery. Unknown hardware sensors must be reported as unknown. Quiet mode caps admissions at one job; Balanced/Performance use owner-configured ceilings. Performance requires local owner approval.

Managed-worker stdout and stderr are redirected into a temporary file under Linux RLIMIT_FSIZE 8 KiB through util-linux prlimit; only 4097 bytes are read into Python. Handlers that emit more than 4096 bytes fail. Workers also request systemd user scopes with MemoryMax, MemoryHigh, MemorySwapMax=0, CPUQuota=150%, CPUWeight=50 and TasksMax=128. Real-host cgroup enforcement remains unverified until installation.

On the phone, the App Runtime tab reports native runner reservations, app RAM/Intel shared-GPU reservations, pressure status, available memory and queued jobs. Approved mobile management users can pause/resume app admissions, switch Quiet/Balanced modes, raise queued-job priority and request an emergency stop of managed app scopes. Performance mode requires local owner access. Emergency-stop first pauses admissions and attempts systemd stops; job leases remain stranded pending safe recovery even when stop succeeded. Native CI jobs are deliberately not killed by app emergency stop.

## Target Garuda laptop

The known target is a Razer Book 13, Intel i7-1165G7, 16 GB RAM and Intel Iris Xe integrated graphics. Intel GPU working memory is part of system RAM, not dedicated NVIDIA VRAM. Start with at least 4096 MiB protected for KDE/OS, no more than three app jobs and one integrated GPU task, while native CI memory reservations are also deducted. Hardware readings and cgroup limits must be verified directly on the actual host.

## Activation

1. Update the checked-out repository on the Garuda host and restart the dashboard and authorised user services. Do not assume merging deploys software.
2. Check the host cgroup v2 systemd user manager, /usr/bin/prlimit, Docker Engine permissions, Intel DRM device IDs and live memory/temp pressure sensors.
3. Use the host-owner application CLI to set global memory headroom and approved per-task budgets. The platform does not assign a private token or new shell to apps without local owner registration.
4. Check existing CI runners and their container limits; restart/recreate authorised runner containers only after reviewing that they have persistent state.
5. Run simultaneous CI and app tasks and validate KDE responsiveness, scope termination on RAM cap/timeout, and mobile pause/resume. No test suite alone certifies real host integration.

## Safety boundaries

Native runner memory accounting is read from Docker inspect and fails closed when the Docker state cannot be inspected. Observed free RAM already includes process use, so subtracting reserved memory is intentionally conservative. Critical pressure throttles new jobs but does not forcibly cancel existing paid CI jobs. A GPU watchdog is not a hard driver-level VRAM quota. Other GPUs and multiple PCs require separate validated probes and fleet scheduling. This is not complete cross-PC orchestration.

See PARALLEL-RESOURCE-SCHEDULER.md and APPLICATION-RUNTIME-API.md for existing API, worker and Intel-specific operating instructions.
## Disk space and retained runner safety

New app-worker and native runner starts react to **root/home writable filesystem** free-space readings: under 2 GiB or under 5% free pauses new admissions; under 4 GiB or under 10% free reduces new app parallelism to one. A missing disk probe is explicitly unknown and does not certify adequate space. The mobile App Runtime dashboard displays the measured disk headroom. Existing running work isn't automatically terminated just because disk is low; protect artifacts using project-specific storage quotas/backups.

The personal GitHub runner pool now refuses to restart **previously created** containers unless Docker confirms finite 2 GiB RAM, no-extra-swap, <=2 CPU, <=256 PIDs, dropped capabilities, no-new-privileges and no automatic Docker restart bypass. New pool runners have those controls from creation. Existing credentials and volumes are never deleted/replaced automatically; manually review and migrate legacy containers before using them. Direct Compose runner services remain opt-in and must be recreated only after backup and explicit user approval. These measures do not constitute per-project filesystem quotas or cover unapproved/out-of-band workloads.
