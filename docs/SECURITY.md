# Security model (community preview v4)

## Scope and assumptions

Open CI Hub manages *native provider runners* and contains a separate opt-in Hub-owned local execution bridge on an operator-controlled PC. It is not a multi-tenant hosted execution platform or a sandbox for malicious workflow code. Do not run untrusted fork pull requests, forks of unknown provenance, or jobs with credentials on a personal machine. Docker containers add boundaries but are **not** isolation against all attacks.

## Authentication and authorization

- The server binds only to `127.0.0.1`. It must never bind to `0.0.0.0` without redesigning authentication, network security and TLS.
- Local dashboard bootstraps from a high-entropy secret in a private `.local/dashboard-secret` file. The launcher temporarily puts it in the URL fragment, which the JS immediately removes and exchanges for a 12-hour `HttpOnly; SameSite=Strict` cookie. Protect browser history, screenshots and local storage. Restarting the server revokes all in-memory sessions. Sign-out revokes the current cookie/session.
- State-changing routes require an authenticated session or approved remote identity, a same-origin Origin and `X-Open-CI-Request: 1`. The API checks roles again server-side, not only in the UI.
- Tailscale Serve terminates HTTPS and injects identity headers. The server validates loopback source, `*.ts.net` host and HTTPS forwarding, then requires separate local-owner approval. Remote approved editors can create repos/control runners and explicitly cancel/retry pipelines, but **cannot** invoke interactive provider login, runner registration or device approval.
- Any program already executing on the PC may be able to spoof the loopback reverse-proxy headers. That host is the trusted computing base: never colocate the console with hostile local users or untrusted local processes.
- Tailscale device identity authenticates access to one running instance, **not** an independently hosted cross-device cloud account. The desktop must stay online.

## Providers and runner credentials

Provider sign-in delegates to official CLIs. Account tokens remain under the CLI's own storage policy, often OS keychain or a permissions-restricted configuration file. The hub never requests you to paste a personal-access token into a form.

Runner registration automatically obtains provider credentials (subject to account permission), then passes them to the official runner. GitHub and GitLab runners persist credentials in Docker volumes, and CircleCI stores an agent token under the ignored `.local/` directory. Docker administrators can access those credentials. Explicitly deregister runners and revoke tokens on the provider to retire them. Deleting the Hub folder alone is insufficient.

Provider CLI calls use argument lists, no shell, strict repository/run ID validation, 30-second timeout and bounded response lengths; failure text does not quote provider CLI stderr. The local audit trail deliberately omits sensitive command output and description data. **CI logs can still contain secrets**: the cockpit links to the original provider rather than fetching logs into Hub.

## Workflow trust

- The Hub-owned bridge is a separate service and is disabled until the local owner initialises private state, approves repositories/branches/images, and installs its user service.
- The bridge accepts only authenticated provider events or authenticated local polling, checks delivery IDs for replay/deduplication, and rejects repositories/branches outside the local allowlist. It checks out the exact event commit and verifies the resulting SHA. Pull/merge-request workflow configuration is read from the protected target branch, and fork requests are rejected.
- Workflow commands are arbitrary *inside the configured job container*, not arbitrary host commands. The container uses a pinned local image ID, read-only root, dropped Linux capabilities, no-new-privileges, bounded CPU/RAM/PIDs/time/output, and no host Docker socket or provider credential mounts. Network access is disabled by default and requires a separate local repository-policy grant. These controls reduce risk; they do not make malicious code safe on a personal computer.
- The bridge defaults to one concurrent job and reserves host RAM/disk. Transient resource pressure is deferred with a cooldown rather than counted as a failed test attempt. A resource guard can stop a running step if host pressure becomes critical.
- Artifacts, logs, step hashes, image IDs and provider-status attempts are retained in private local state. A green provider status is not independent attestation; verify the job ID, commit SHA, evidence and artifacts.
- Production deployment is separate from build/test shell steps. It requires a locally allowlisted release application and signer, an owner-attached offline signature, a release manifest whose source commit matches the job SHA, and explicit local approval. Do not configure production deployment until the signed-release manager has been reviewed for that application.
- The webhook server binds to loopback only. Provider webhooks require a separately secured HTTPS/private reverse proxy and strong per-provider secret; never expose the bridge port directly to the public internet. Authenticated outbound polling is the default fallback.

- Separate repositories and native runners by trust level. GitLab starter is project-scoped, protected ref and tagged `local-ci` only. GitHub runner should be repository-scoped for trusted code.
- There is no `/var/run/docker.sock` bind mount or home folder bind mount in the shipped Compose file. Consequently nested Docker/container builds are not supported by default.
- A compromised CI job may affect later jobs on persistent runner state. Use disposable VMs for higher-risk work. Keep deploy credentials distinct from CI credentials and require production approvals outside the hub.
- Container image tags are pinned at project release time but updates require an operator review. Re-validate when updating them.

## Threats and outstanding work

The app has unit/integration tests, not a third-party penetration test. It has not been validated against real provider auth flows, Docker runner images, local macOS/Windows installers, CircleCI plan policies or self-managed GitLab. Do not publish instance credentials, tokens or private security reports to public issues. When published, enable private security advisories/reporting.

For higher-assurance use: independent security review; one-time bootstrap/rotation mechanism; per-project scoped editor permissions; tamper-evident audit log, disposable worker isolation; signed distribution, SBOM and auto-update; and a properly deployed hosted OIDC control plane for cloud/mobile independence.
