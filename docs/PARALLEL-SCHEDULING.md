# Parallel projects: safeguarded RAM, VRAM and CPU admission

Open CI Hub's Application Runtime now supports **concurrent jobs across independently authorised applications**, subject to a shared resource budget on each compute PC. The scheduler uses a single SQLite BEGIN IMMEDIATE transaction for each job claim, so two application workers cannot reserve the same memory, GPU VRAM, CPU slots or concurrency slot. Projects can run at once when their combined reservations fit.

## Important protection boundary

This is **cooperative admission control**. It does not impose a hard cgroup/process RAM limit, virtualise GPU VRAM, or automatically kill a misbehaving model. Both memory and VRAM reservations are based on owner-configured caps plus recent worker-reported measurements and are **not independent hardware attestations**. For production workloads, the worker runtime must additionally use OS isolation (e.g. systemd cgroup MemoryMax, CPUQuota/CPUWeight and process kill on lease expiry) and conservative GPU headroom monitoring. GPU VRAM hard limits are not portable across NVIDIA/AMD and cannot be guaranteed by the scheduler alone.

No job acquires sudo/root or direct host command privileges. This is not CI runner scheduling: independent trusted application workers are responsible for task execution. Do not run untrusted customer code using a persistent desktop shell.

## How to configure before activating on the host

Configure the planned Garuda node capacity deliberately below physical capacity. Example only — **inspect actual RAM/VRAM first**. The values below are illustrative, NOT detection of your PC:

~~~bash
# Total node capacity 32 GiB RAM and one 12 GiB NVIDIA GPU.
# Default reserve: at least 25% RAM and 12.5% VRAM, with minimum buffers.
python3 app_runtime.py node garuda-pc \
  --memory-mb 32768 --vram-mb 12288 --cpu-slots 8 \
  --max-parallel 4 --confirm

# Each registered project receives strict task profiles:
python3 app_runtime.py task-resources raeburnemailer prepare \
  --memory-mb 1024 --vram-mb 0 --cpu-slots 1 --confirm
python3 app_runtime.py task-resources trg-recruitment cv-process \
  --memory-mb 2048 --vram-mb 0 --cpu-slots 2 --confirm
python3 app_runtime.py task-resources clientmotive video-render \
  --memory-mb 6144 --vram-mb 8192 --cpu-slots 4 --confirm

# Give each project its own maximum concurrency:
python3 app_runtime.py app-limit raeburnemailer --max-parallel 2 --confirm
python3 app_runtime.py app-limit trg-recruitment --max-parallel 2 --confirm
python3 app_runtime.py app-limit clientmotive --max-parallel 1 --confirm

# Workers MUST be bound to an approved node when parallel scheduling is enabled.
# Reissue pre-existing worker credentials with --node; submit credentials are unchanged.
python3 app_runtime.py issue raeburnemailer worker --node garuda-pc --confirm
python3 app_runtime.py resources
~~~

Applications and approved tasks must already be registered using the [Application Runtime API guide](APPLICATION-RUNTIME-API.md). Keep API tokens in the worker's private secret store and revoke old unbound worker tokens once node-bound replacements are operational.

**Important:** Register a node ONLY with reliable host measurements, and don't overstate total free RAM/VRAM. A node represents a single PC and, in this first version, at most **one selected NVIDIA GPU**. A multi-GPU machine should not register each GPU as a separate full-RAM node: that would double-count system memory. AMD/Intel GPU VRAM auto-detection is not yet implemented and will fail closed for VRAM-bound tasks.

## Safety logic for each claim

1. Verify the worker Bearer token belongs to the correct app AND explicitly approved node. New node-bound worker tokens are required once any compute node exists.
2. Require the task to have an **owner-configured** RAM, VRAM and CPU profile. Unprofiled tasks remain queued.
3. Require fresh (30-second) Linux worker telemetry. The reference client measures Linux /proc/meminfo MemAvailable, clamps to cgroup memory headroom when available and asks nvidia-smi for one GPU's free VRAM. Missing/unavailable NVIDIA telemetry yields 0 VRAM, blocking GPU requests.
4. Under one transactional SQLite lock, subtract live reservations from the configured node capacity and preserve OS memory/VRAM reserves. Also compare the new request to instantaneous free RAM and VRAM **after a safety buffer**. CPU slot and total parallel job limits are enforced.
5. Enforce per-app concurrency, preventing one busy project from consuming every slot. If resources are exhausted, return a reason and keep the job queued. Smaller eligible jobs may proceed while a larger one waits.
6. Lease the work atomically for five minutes. Worker heartbeats renew the lease; acknowledgement or failure removes its reservation. Expired leases can be retried up to three times, but the worker must be configured to **terminate the old process** at lease expiry to prevent oversubscription.

Each machine has independent RAM/VRAM budgets. To add PCs, enrol them separately with an owner-approved node configuration and issue worker credentials bound to that machine. The app queue can then share capacity across approved machines without passing PC control tokens between them.

## Monitoring

The mobile and desktop **App Runtime** tab shows each compute node's active jobs, reserved memory/VRAM/CPU, and remaining configured capacity. The UI explicitly labels these values as reservations rather than direct hardware proof.

The app worker API request to /api/v1/workers/claim now supports:

~~~json
{
  "telemetry": {
    "memory_available_mb": 14000,
    "vram_available_mb": 6500,
    "observed_at": 1780000000.0,
    "source": "linux-worker-preflight"
  }
}
~~~

Use the included Python ApplicationClient.claim() method to sample telemetry automatically; do not hard-code or forge these values. The timestamp example is illustrative and will expire. The response includes reservation memory_mb/vram_mb/cpu_slots and the selected node ID. Failures may include app_concurrency_limit, node_concurrency_limit, resource_unavailable_or_profile_missing or an explicit validation denial.

## Remaining for actual hard protection and scaling

- cgroups v2 / systemd-run user-scope hard **MemoryMax** and CPU quotas for every executed app task; verified with controlled out-of-memory tests and automatic process cleanup.
- GPU process-level tracking, hard admission and overrun eviction across NVIDIA GPU processes; AMD/Intel support and multi-GPU accounting.
- Worker liveness/proof-of-execution, telemetry signing/identity assurance, explicit task timeouts, lease-driven cancellation and job-specific resource usage audit.
- Backpressure, priority policy, fairness and scheduling across many PCs, including offline node detection.
- Multi-PC networking, protected deployment service credentials, and load testing on real Garuda RAM/GPU configurations.

**Do not treat these reservations as strict OS resource limits until the worker executor and real hardware checks are deployed and verified.**
