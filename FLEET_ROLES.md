# Fleet roles — 30 October 2026

Razer Book 13: controller, source validation and bounded x86 builds. Preserve foreground RAM.
Galaxy Book Go (Debian ARM64): private small-model inference and approved lightweight ARM tasks while on mains. Current Vulkan reports software llvmpipe, so hardware GPU acceleration is unverified.
S25: approved short Python syntax, JSON inspection and hash jobs. Existing battery floor remains enforced. Prefer powered candidates.
S22 Ultra: proposed secondary inference/worker role; blocked by stale worker and unavailable management ports. Tailnet presence alone is insufficient. Restore existing Termux supervisor on-device before dispatch.
iPhone: owner control and real iOS/browser testing; no Hub compute worker.
Offline Windows desktop: inventory hardware after reconnection before assigning build capacity.
Old Samsung Windows registration: retain as historical until ownership/duplicate identity is reconciled; do not count as additional hardware.

## Applied behavior

Linux workers report actual battery/charger telemetry. Paused workers continue heartbeats without claiming work. Fleet status requires a fresh heartbeat and separates alive from eligible. Pool includes the Linux ARM laptop, uses its configured RAM reservation, and prefers powered devices. CLI health refreshes worker availability while retaining historical monitor evidence.

Backups were retained before changing active source and the laptop worker. No authentication material was moved, no task allowlist widened, and unrelated checkout changes were preserved.

## Validation

Targeted suite: 49 tests passed. Full scripts/validate.py exited zero before application. Remote worker restarted successfully and directly reported unplugged at 80 percent. S22 recovery and offline-device verification remain blocked by unavailable management connections.

Live deployment evidence: the laptop's older application_client.py lacked battery_percent; the first restart failed and was immediately rolled back. Deploying the current tested client with the worker restored service. The next heartbeat reported worker_alive=true, worker_eligible=false on battery. A bounded JSON job completed with an independently verified receipt during rollback recovery; it is not evidence of the new battery gate.
