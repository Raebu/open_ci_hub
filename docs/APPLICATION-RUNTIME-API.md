# Application Runtime API — Phase 1

Open CI Hub can power internal applications through a **private, durable, typed job API** without sharing CI runner credentials or the host terminal. Apps submit pre-approved JSON jobs; separate app-specific workers fetch them, execute their own application code and acknowledge success/failure. The Hub itself never interprets job payloads as shell commands.

**Status:** Source implementation and self-hosted CI tests. Requires installation/activation of Open CI Hub on a trusted host for real application use. This is *not* a cloud SaaS, public multi-tenant API, deployment controller, or a claim that an app worker is installed.

## Security and trust

- Existing web dashboard stays on **127.0.0.1 only**. A remote service must use owner-configured **Tailscale Serve HTTPS** (not Funnel), a valid approved Tailscale identity, and its individual Bearer token.
- API credentials are 256-bit-class randomly generated secrets issued **only by the host owner locally**, with SHA-256 hashes stored in a private SQLite database. Tokens are shown once and must never be committed, pasted to chat, placed in frontend JavaScript or printed into CI logs. Token issuance, app registration and revocation have NO HTTP endpoint.
- One application gets a separately scoped **submit** token and **worker** token. A submitter cannot claim/ack jobs; a worker cannot submit. Neither role can create apps, manage host services, run terminal commands, access CI provider tokens, or deploy releases.
- Jobs are scoped to their application's allowlisted task names. Data is bounded JSON (2 KiB), not code. The Hub does not execute task payloads; the separately installed application worker is responsible for safely implementing each task, applying business-level authorisation, and avoiding secrets in job payloads.
- Default queue limits: 100 queued/leased jobs per app, 10,000 retained jobs globally, three attempts per job and a five-minute worker lease. Call heartbeat during long tasks. Completed/failed records are eligible for removal seven days after completion.
- **At-least-once processing**, not exactly-once: when a worker crashes after completing an external side effect but before acknowledging, the lease may expire and work may be retried. Every worker MUST implement idempotency against side effects (payments, email sending and billing). The submission idempotency key prevents duplicating a job record, but cannot guarantee exactly-once external action.
- No external network endpoints, root capabilities or docker.sock mounts are handed to app job tokens. Compute isolation, quotas and sandboxing depend on how the separate worker is deployed and are NOT implemented by this queue alone.
- API clients do not use browser cookies. All endpoints require an application-specific Bearer token; browsers never receive an unscoped host token. Never expose it via Tailscale Funnel/public internet.

## Owner setup (after installing the app on Garuda)

On the host, from the Open CI Hub source folder:

~~~bash
# Register one application and exactly its approved operations:
python3 app_runtime.py register raeburnemailer --tasks prepare,dispatch --confirm

# Issue two distinct, one-time credentials. Store them in your service's secret store.
python3 app_runtime.py issue raeburnemailer submit --confirm
python3 app_runtime.py issue raeburnemailer worker --confirm

# To revoke an exposed key: compute its SHA256 locally, then:
python3 app_runtime.py revoke THE_TOKEN_SHA256 --confirm
~~~

Register TRG Recruitment or other products using their own app ID and task names. Never give RaeburnEmailer's tokens to the recruitment service.

Restart the Open CI Hub dashboard after pulling the new revision. Its existing private 127.0.0.1 HTTP listener hosts the API at /api/v1/. Apps on the same PC can call http://127.0.0.1:8765 (or your configured port). Apps on another authorised machine need a protected Tailscale network connection and valid approved identity to that specific Hub; these remote runtime calls are **not yet validated against live Tailscale Serve**.

## HTTP API

All endpoints require Authorization: Bearer oci_v1_... and JSON request bodies for POST.

| Method | Endpoint | Scope | Description |
|---|---|---|---|
| GET | /api/v1/apps/self | Either | App, scope and aggregate queue state |
| POST | /api/v1/jobs | submit | Enqueue one allowlisted task |
| GET | /api/v1/jobs/JOB_ID | Either | Read your own app's status, attempts and result |
| POST | /api/v1/workers/claim | worker | Atomically lease the next queued task |
| POST | /api/v1/workers/heartbeat | worker | Renew a still-valid lease |
| POST | /api/v1/workers/complete | worker | Finish a task or retry on failure |

Example submit body:

~~~json
{
  "task": "prepare",
  "payload": {"document_id": "sample-0123"},
  "idempotency_key": "customer-request-012345"
}
~~~

The API returns a random job_id and queued/duplicate status. An independently authenticated worker calls /api/v1/workers/claim, receives task, payload, job_id and a short-lived lease_token, executes the registered application handler (not shell), then acknowledges:

~~~json
{
  "job_id": "0123456789abcdef0123456789abcdef",
  "lease_token": "issued-by-claim",
  "success": true,
  "result": {"ok": true}
}
~~~

The Python standard-library client is in application_client.py; it validates localhost or Tailscale MagicDNS endpoints and TLS certificates, never follows HTTP redirects, and has methods submit(), claim(), heartbeat(), complete() and status().

## Why this is useful and what comes next

- RaeburnEmailer can enqueue a **prepare** operation and have a separate worker generate an email draft, without giving it access to CI runners or a host shell. A later **dispatch** worker must implement delivery idempotency and consent validation to prevent duplicate sends.
- TRG Recruitment can enqueue a CV processing task into an isolated, privacy-compliant application worker. Sensitive personal data should reside in the application's secure storage; pass a short opaque ID in the queue rather than CV content.
- A GPU application can schedule its own processing tasks via the same API with a distinct worker token. This initial queue does not reserve GPU, route among multiple PCs, or enforce CPU/memory limits; those need a separate scheduler and sandbox.

**Pending before production:** authenticated cross-PC integration tests; API key rotation and per-request usage auditing; rate limits beyond queue quotas; optional webhook event delivery; separately sandboxed app worker lifecycle and deployment; HTTPS/OIDC and security audit; monitoring, backups and credential revocation drill. The operational host agent, runners and signing key distribution remain separate.

Use HTTPS on your tailnet and do not use Tailscale Funnel for any API with a persistent token.

## Parallel execution and budget controls

The broker now enforces global and per-project concurrent lease quotas and per-task owner-defined RAM/GPU VRAM reservations when workers claim jobs. Claims provide the approved resources to the worker. A managed systemd user worker, managed_worker.py, can enforce RAM via MemoryMax and inspect NVIDIA VRAM with a watchdog. This does not automatically govern existing provider-native CI jobs. Worker leases that expire are held in a stranded state until verified dead rather than reassigned while they might still be consuming RAM/VRAM. See [parallel resource scheduler](PARALLEL-RESOURCE-SCHEDULER.md) for activation, hardware and GPU limitations.
