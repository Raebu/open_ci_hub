# Parallel project scheduling — RAM and VRAM safeguards

Open CI Hub's app runtime can admit **multiple projects concurrently** on the same PC, while holding global and per-project capacity reservations. This initial scheduler runs alongside (not *inside*) GitHub/GitLab/CircleCI runners. Existing CI runner jobs are **not** included in its reservation accounting. Project-specific app workers must use the managed runner described below to apply enforceable RAM ceilings.

## What is enforced

- A single SQLite BEGIN IMMEDIATE gate prevents two worker requests from simultaneously reserving the same remaining capacity.
- By default: at most **four parallel app jobs globally**, **two per project**. Host owner can change both.
- Each task has an owner-approved RAM allocation (default 512 MiB). GPU tasks must have an explicitly configured VRAM reservation and GPU ID.
- CPU-only and mobile-edge jobs no longer inherit a blanket multi-GiB free-RAM floor. The current host uses a **256 MiB base reserve**; each task's approved RAM budget must still fit after active app/native-runner reservations. Below 5 GiB available RAM, host-side concurrency falls to one rather than blocking every small task. Critical swap exhaustion (under 512 MiB free), critical memory pressure, thermal/disk limits and other critical host conditions still pause new host-side work. An unavailable RAM sensor fails closed. Dedicated GPUs retain a VRAM reserve.
- For dedicated NVIDIA GPUs, bounded nvidia-smi telemetry enforces admission headroom. **For known Intel Iris Xe Tiger Lake integrated graphics, there is no separate VRAM pool**: the task's vram_mb parameter means *additional shared system-memory working-set budget* and counts against RAM headroom and the worker's cgroup MemoryMax. One integrated GPU task may run at a time; CPU-only tasks can continue. Unknown GPU devices fail closed for GPU work. Neither Intel nor NVIDIA GPU allocations have a universal hard GPU-memory cap.
- An unavailable RAM sensor prevents admission rather than assuming unlimited memory.
- Jobs that lose their lease become **stranded** rather than auto-reclaimed. Their resource reservations remain held until the host owner verifies that the old worker process has exited and explicitly clears the stranded job. This avoids quietly doubling resource usage and side effects.
- For CPU/RAM jobs, the optional managed_worker.py invokes fixed, local-owner-approved handlers through a **systemd --user --scope** unit with MemoryMax, MemorySwapMax=0 and TasksMax; if the systemd user manager/cgroup enforcement isn't available, it does not launch unbounded work.
- For dedicated NVIDIA jobs, managed_worker.py checks dedicated free VRAM and monitors its 1 GiB buffer. **For Intel integrated jobs**, the worker verifies the Intel GPU device, checks system RAM against the combined RAM+shared-GPU reservation and preserves a 4 GiB desktop floor, including during the job. This additional floor applies to shared-GPU work only, not ordinary CPU-only or mobile-edge jobs. It requests termination if telemetry fails or RAM headroom is breached. This is a *software watchdog*: allocation spikes and external applications can still OOM a GPU before the next sample. Use separate GPUs or process isolation if stricter guarantees are needed.

**This scheduler does not claim to isolate an arbitrary executable running outside managed_worker.py**, and does not manage existing CI runner container resource usage. Worker participation is essential.

## Intel Iris Xe (Razer Book 13, 16 GB system RAM)

A Core i7-1165G7 with Intel Iris Xe does **not** have an independent 6–12 GB NVIDIA VRAM pool. Its graphics allocations consume the same physical RAM used by Garuda, KDE, CI and applications.

The scheduler checks Linux DRM PCI vendor/device IDs to identify supported Intel Tiger Lake Iris Xe integrated GPU devices. If those IDs or DRM interfaces aren't visible (for example from inside a CI runner container), it refuses GPU admissions instead of inventing memory. On a confirmed 16 GB Intel desktop, default admission reserves **4096 MiB for the system** and limits the queue to **three concurrent app jobs**, of which **one** can be an integrated-GPU task. Explicit owner-configured limits still override these defaults. Before running, read `python3 app_runtime.py resources` on the actual host to confirm detected memory/GPU. Worker limits cannot protect unmanaged CI runner containers; account for them when setting budgets.

**Terminology:** the existing `--vram-mb` option remains for compatibility, but on Intel Iris Xe it is an extra shared-RAM budget rather than dedicated VRAM. Example: `--ram-mb 2048 --vram-mb 2048 --gpu-index 0` reserves 4096 MiB *total system RAM* and sets `MemoryMax=4096M` for an opted-in managed worker. GPU allocations are not always charged to cgroups, so monitoring and real-host testing remain essential.

## Owner configuration (only after updating the PC)

~~~bash
cd /mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub
git pull --ff-only origin main

# Global queue concurrency and OS memory headroom.
python3 app_runtime.py global-resources --max-parallel 3 --min-free-ram-mb 4096 --min-free-vram-mb 0 --confirm

# Existing apps should first be registered as in docs/APPLICATION-RUNTIME-API.md
python3 app_runtime.py parallel raeburnemailer --max-parallel 2 --confirm
python3 app_runtime.py budget raeburnemailer prepare --ram-mb 512 --confirm

python3 app_runtime.py parallel clientmotive --max-parallel 1 --confirm
python3 app_runtime.py budget clientmotive video --ram-mb 2048 --vram-mb 2048 --gpu-index 0 --confirm

python3 app_runtime.py resources
~~~

These are examples, not measurements of your actual Garuda hardware. Confirm the integrated Intel GPU appears as `mode: shared` in the resource inventory and check available RAM before approving values. Do not use nvidia-smi as a prerequisite on this laptop. No dedicated GPU memory total should be displayed for Iris Xe.

The mobile **App Runtime** dashboard shows active jobs, reserved RAM/VRAM, per-app concurrency and whether GPU telemetry is available.

## Managed app worker example

The host owner creates a **private mode-0600, user-owned JSON handler file**. It contains local, fixed executable paths, not commands from HTTP request payloads:

~~~json
{
  "prepare": {
    "argv": ["/usr/bin/python3", "/opt/raeburnemailer/workers/prepare.py"],
    "timeout": 240
  }
}
~~~

Each handler reads JSON from standard input and writes one JSON object/array to standard output, exits nonzero on failure. Workers MUST be idempotent when performing mail, payment, database or external side effects, because queue processing is at least once. The handler may not implement remote shell execution.

~~~bash
chmod 600 /home/theraeburngroup/raeburn-emailer-handlers.json
# Set the worker-only key in the host service's protected environment, NEVER in git:
export OPEN_CI_WORKER_TOKEN='<the locally issued worker key>'
python3 managed_worker.py --url http://127.0.0.1:8765 --handlers /home/theraeburngroup/raeburn-emailer-handlers.json --once
~~~

Do not paste production tokens into chat, terminal recordings or shared scripts; the export above is illustrative only. A production worker should obtain its token from an appropriately protected service credential rather than shell history. Repeat worker processes/services per app for parallel processing. They all coordinate against the same SQLite database.

Under systemd user sessions, enabling MemoryMax can require cgroup-v2 delegation or a suitable systemd version. Test on the real PC and validate memory.max for the transient scope. The memory/cgroup tests in CI are mocked; they do not establish that your specific desktop authorises systemd scopes.

## Operational limits before production

1. Measure real system RAM/GPU capacities, reserved desktop needs, and other running CI/desktop workloads; tune budgets accordingly. A machine's actual free RAM/VRAM is dynamic.
2. Verify multiple concurrently running CPU and GPU app jobs on the real host with cgroup hard-memory caps and the GPU watchdog; measure peaks and abort paths.
3. Add an independent fleet-wide scheduler to coordinate jobs on **multiple PCs**. Currently admission is central only to one app-runtime SQLite store.
4. Integrate GitHub/GitLab/CircleCI runner capacity into global accounting. Existing runner jobs are outside this admission gate and could still compete for RAM/VRAM.
5. Add a supervisor with durable process ownership and automated orphan termination before unattended retries. Until then, lease expiry intentionally strands tasks for explicit safe recovery.
6. Expand Intel GPU support beyond the known Tiger Lake IDs and add AMD/other vendor probes without guessing GPU memory totals. NVIDIA dedicated VRAM is not hard-isolated by the watchdog, and Intel shares host RAM.

For a stranded job after confirming that its original handler scope is no longer running:

~~~bash
python3 app_runtime.py recover-stranded FULL_JOB_ID --confirm
~~~

Do not recover merely because the phone/dashboard disconnected. Confirm the scope has ended, or two instances may perform the same side effect.
