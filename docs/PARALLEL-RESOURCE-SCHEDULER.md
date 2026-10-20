# Parallel project scheduling — RAM and VRAM safeguards

Open CI Hub's app runtime can admit **multiple projects concurrently** on the same PC, while holding global and per-project capacity reservations. This initial scheduler runs alongside (not *inside*) GitHub/GitLab/CircleCI runners. Existing CI runner jobs are **not** included in its reservation accounting. Project-specific app workers must use the managed runner described below to apply enforceable RAM ceilings.

## What is enforced

- A single SQLite BEGIN IMMEDIATE gate prevents two worker requests from simultaneously reserving the same remaining capacity.
- By default: at most **four parallel app jobs globally**, **two per project**. Host owner can change both.
- Each task has an owner-approved RAM allocation (default 512 MiB). GPU tasks must have an explicitly configured VRAM reservation and GPU ID.
- Admission requires at least **2,048 MiB remaining RAM headroom** and **1,024 MiB free VRAM headroom** *after conservative existing reservations*. Linux RAM telemetry also respects the process cgroup-v2 memory capacity when available.
- Nvidia GPU VRAM total/free readings use a bounded 3-second nvidia-smi probe. AMD, Intel and GPU setups without supported telemetry **fail closed for GPU tasks** (CPU tasks continue). VRAM reservations do not constitute hardware-enforced VRAM limits.
- An unavailable RAM sensor prevents admission rather than assuming unlimited memory.
- Jobs that lose their lease become **stranded** rather than auto-reclaimed. Their resource reservations remain held until the host owner verifies that the old worker process has exited and explicitly clears the stranded job. This avoids quietly doubling resource usage and side effects.
- For CPU/RAM jobs, the optional managed_worker.py invokes fixed, local-owner-approved handlers through a **systemd --user --scope** unit with MemoryMax, MemorySwapMax=0 and TasksMax; if the systemd user manager/cgroup enforcement isn't available, it does not launch unbounded work.
- For GPU tasks on NVIDIA, managed_worker.py checks free VRAM before launch and continuously monitors a minimum 1 GiB free buffer; it requests the systemd scope stop if the buffer falls too low or telemetry fails. This is a *software watchdog*: allocation spikes and external applications can still OOM a GPU before the next sample. Use separate GPUs or process isolation if stricter guarantees are needed.

**This scheduler does not claim to isolate an arbitrary executable running outside managed_worker.py**, and does not manage existing CI runner container resource usage. Worker participation is essential.

## Owner configuration (only after updating the PC)

~~~bash
cd /mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub
git pull --ff-only origin main

# Global queue concurrency and OS memory headroom.
python3 app_runtime.py global-resources --max-parallel 4 --min-free-ram-mb 2048 --min-free-vram-mb 1024 --confirm

# Existing apps should first be registered as in docs/APPLICATION-RUNTIME-API.md
python3 app_runtime.py parallel raeburnemailer --max-parallel 2 --confirm
python3 app_runtime.py budget raeburnemailer prepare --ram-mb 512 --confirm

python3 app_runtime.py parallel clientmotive --max-parallel 1 --confirm
python3 app_runtime.py budget clientmotive video --ram-mb 4096 --vram-mb 6144 --gpu-index 0 --confirm

python3 app_runtime.py resources
~~~

These are examples, not measurements of your actual Garuda hardware. Check memory_available_mb and nvidia-smi results before approving values. Do not set VRAM budgets to the entire advertised GPU capacity.

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
6. Install VRAM probes for other GPU vendors and verify CUDA_VISIBLE_DEVICES behaviour. NVIDIA VRAM is not hard-isolated even with a watchdog.

For a stranded job after confirming that its original handler scope is no longer running:

~~~bash
python3 app_runtime.py recover-stranded FULL_JOB_ID --confirm
~~~

Do not recover merely because the phone/dashboard disconnected. Confirm the scope has ended, or two instances may perform the same side effect.
