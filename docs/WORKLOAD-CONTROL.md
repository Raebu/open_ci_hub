# Unified Workload Control

## What changed

Open CI Hub now compares application reservations against observed native GitHub, GitLab, CircleCI and pooled GitHub Docker container memory before admitting more app jobs. It also guards provider and pool runner starts. Existing Docker Compose CI services receive 2 GiB memory, 2 GiB memory+swap, 1.5 CPUs and 256 PIDs each. External runners, unbounded child containers and independent cloud workers are not included.

The app runtime samples Linux memory PSI, relevant package thermal sensors, CPU load and laptop battery. It reduces new-job admission under moderate pressure and pauses new work during critical heat, memory pressure or low battery. Unknown hardware sensors must be reported as unknown. Quiet mode caps admissions at one job; Balanced/Performance use owner-configured ceilings. Performance requires local owner approval.

Managed-worker stdout and stderr now flow through a bounded 4 KiB reader. Exceeding the limit stops the whole managed scope, and an unverified scope stop leaves the job reserved for reconciliation. Crucially, this does **not** set RLIMIT_FSIZE on the worker process, so approved applications can still create normal document, CV and large artifact files. The output cap applies to logs/JSON returned through the worker protocol; artifacts must use application-specific secure storage. Workers also request systemd user scopes with MemoryMax, MemoryHigh, MemorySwapMax=0, CPUQuota=150%, CPUWeight=50 and TasksMax=128. Real-host cgroup enforcement remains unverified until installation.

On the phone, the App Runtime tab reports native runner reservations, app RAM/Intel shared-GPU reservations, pressure status, available memory and queued jobs. Approved mobile management users can pause/resume app admissions, switch Quiet/Balanced modes, raise queued-job priority and request an emergency stop of managed app scopes. Performance mode requires local owner access. Emergency-stop first pauses admissions and attempts systemd stops; job leases remain stranded pending safe recovery even when stop succeeded. Native CI jobs are deliberately not killed by app emergency stop.

## Target Garuda laptop

The known target is a Razer Book 13, Intel i7-1165G7, 16 GB RAM and Intel Iris Xe integrated graphics. Intel GPU working memory is part of system RAM, not dedicated NVIDIA VRAM. The live host's base free-RAM reserve is now 256 MiB; the blanket 4 GiB floor has been removed for CPU-only and mobile-edge work. Each job's approved RAM budget must still fit in available memory after active app/native-runner reservations. Below 5 GiB available RAM, host-side concurrency is throttled to one job. New host work still pauses when free swap drops below 512 MiB, memory PSI reaches critical pressure, or critical thermal/disk/battery conditions occur. Intel shared-memory GPU tasks retain a 4 GiB floor because their working set comes from the same physical RAM. Mobile edge tasks use device-specific RAM, battery, Wi-Fi and one-job-per-device checks rather than reserving their task memory against the laptop.

## Activation

1. Update the checked-out repository on the Garuda host and restart the dashboard and authorised user services. Do not assume merging deploys software.
2. Check the host cgroup v2 systemd user manager, /usr/bin/prlimit, Docker Engine permissions, Intel DRM device IDs and live memory/temp pressure sensors.
3. Use the host-owner application CLI to set global memory headroom and approved per-task budgets. The platform does not assign a private token or new shell to apps without local owner registration.
4. Check existing CI runners and their container limits; restart/recreate authorised runner containers only after reviewing that they have persistent state.
5. Run simultaneous CI and app tasks and validate KDE responsiveness, scope termination on RAM cap/timeout, and mobile pause/resume. No test suite alone certifies real host integration.

## Safety boundaries

Native runner memory accounting is read from Docker inspect and fails closed when the Docker state cannot be inspected. Observed free RAM already includes process use, so subtracting reserved memory is intentionally conservative. Critical pressure throttles new jobs but does not forcibly cancel existing paid CI jobs. A GPU watchdog is not a hard driver-level VRAM quota. Other GPUs and multiple PCs require separate validated probes and fleet scheduling. This is not complete cross-PC orchestration.

See PARALLEL-RESOURCE-SCHEDULER.md and APPLICATION-RUNTIME-API.md for existing API, worker and Intel-specific operating instructions.
**Safety:** The worker does not acknowledge and release a reservation on abnormal termination unless systemd reports that the managed scope is inactive with an empty cgroup. Otherwise it reports `stranded` locally and the lease eventually expires into the broker's stranded state, preventing unsupervised retry.
