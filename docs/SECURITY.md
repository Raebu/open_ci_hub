# Security model (community preview v4)

## Scope and assumptions

Open CI Hub manages *native provider runners* on an operator-controlled PC. It is not a multi-tenant hosted execution platform or a sandbox for malicious workflow code. Do not run untrusted fork pull requests, forks of unknown provenance, or jobs with credentials on a personal machine. Docker containers add boundaries but are **not** isolation against all attacks.

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

- Separate repositories and native runners by trust level. GitLab starter is project-scoped, protected ref and tagged `local-ci` only. GitHub runner should be repository-scoped for trusted code.
- There is no `/var/run/docker.sock` bind mount or home folder bind mount in the shipped Compose file. Consequently nested Docker/container builds are not supported by default.
- A compromised CI job may affect later jobs on persistent runner state. Use disposable VMs for higher-risk work. Keep deploy credentials distinct from CI credentials and require production approvals outside the hub.
- Container image tags are pinned at project release time but updates require an operator review. Re-validate when updating them.

## Threats and outstanding work

The app has unit/integration tests, not a third-party penetration test. It has not been validated against real provider auth flows, Docker runner images, local macOS/Windows installers, CircleCI plan policies or self-managed GitLab. Do not publish instance credentials, tokens or private security reports to public issues. When published, enable private security advisories/reporting.

For higher-assurance use: independent security review; one-time bootstrap/rotation mechanism; per-project scoped editor permissions; tamper-evident audit log, disposable worker isolation; signed distribution, SBOM and auto-update; and a properly deployed hosted OIDC control plane for cloud/mobile independence.
