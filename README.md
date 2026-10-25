# Open CI Hub

**Mandatory operating constraint:** [zero-additional-subscription, self-hosted-first policy](docs/ZERO-COST.md). Provider retries and cloud promotions are blocked where £0 exposure cannot be verified; existing hosting is preserved.

An **open-source, provider-neutral, local-first CI/CD control centre** for GitHub Actions, GitLab CI/CD and CircleCI. Use the providers' native self-hosted runners to build on a computer you control instead of purchasing hosted runner minutes.

**Release: v4 community preview; native onboarding is implemented in source and requires live verification.** Local Docker image builds/bootstrap and read-only provider checks have passed; real push builds across all three providers remain unverified. Do **not** treat it as a security-audited or production-certified service.

Delivery and cross-chat handover: [canonical project checklist](https://github.com/Raebu/open_ci_hub/issues/48), [current verified state](docs/PROJECT-HANDOVER.md) and [reusable project continuity standard](docs/PROJECT-CONTINUITY-STANDARD.md).

For the optional unattended provider watcher and local resource-aware build worker, see [streamlined autonomous CI](docs/STREAMLINED-AUTONOMOUS-CI.md).

See the [engineering delivery and evidence](docs/ENGINEERING-2026-10-19.md) for
current validation, activation commands and remaining release gates.

## Highlights

- Responsive dashboard with **Sign in** and **Register runner** buttons. Authorization uses the **official provider CLIs and browser**, not copy/paste API keys.
- Native provider runner support: GitHub Actions, GitLab, CircleCI, with separate Docker Compose profiles. No host Docker socket or home-directory mount by default.
- Draft Hub-owned CI trigger bridge: authenticated GitHub/GitLab webhooks, exact-SHA queueing, and shared resource-aware parallel admission through the existing application broker. Not yet wired into the installer/dashboard or production-verified; see [bridge implementation and release gates](docs/HUB-OWNED-CI-BRIDGE.md).
- Browse GitHub/GitLab repositories, create private or public repositories (with confirmation), inspect recent pipelines, and explicitly retry/cancel GitHub or GitLab runs.
- Control runners, inspect Docker/disk/CLI readiness, and view a **local SQLite management audit log**.
- Optional **mobile access through Tailscale Serve**, using a trusted tailnet identity with an owner-approved viewer or editor role.
- MIT licence, neutral branding, contribution policy, workflow templates, installation docs and tests.

**Important:** Mobile access uses the same live PC-backed workspace. It does **not** sync to a hosted account while the PC is offline. GitHub/GitLab/CircleCI require separate permissions. CircleCI pipeline browsing/retry is not yet implemented: its runner integration is available through the installer and runner controls. Provider-hosted storage/networking and plan limits may still apply.

## Private edge fleet and visual autonomy (reviewed source)

The [unified autonomy release guide](docs/UNIFIED-AUTONOMY-20261025.md) adds
owner-enrolled Windows ARM64, Linux and Android fixed-operation workers over
the existing Tailscale-protected app API, live device admission and fleet
readiness in the mobile dashboard, local Ollama screenshot observation,
a bounded language-plan composer, private Xvfb lifecycle, and Ed25519
owner evidence manifests. These do **not** authorise arbitrary remote shell,
live paid cloud VMs or production release automation. Actual phone/Windows
enrolment, Xvfb installation and independent security tests remain required.

## Branch hygiene and automatic cleanup (opt-in)

The [verified branch cleanser](docs/BRANCH-CLEANSER.md) audits GitHub against immutable commit SHAs, PR state and local worktrees, then archives exact tips before safely retiring only merged inactive branches after 72 hours. A resource-capped daily timer is available via explicit owner enrolment; unique/unmerged work is never automatically deleted.

## Human-first background autonomy (opt-in)

A headless Chromium worker can process locally authorised, bounded web/data
missions without taking focus or keyboard/mouse input from KDE. CPU/RAM limits,
HTTPS host allowlists, evidence receipts, a low-impact daemon and a read-only
owner dashboard are provided. This is not a general remote desktop or an
unrestricted CI agent. See [background autonomy](docs/BACKGROUND-AUTONOMY.md)
for installation, verified outcomes and remaining security gates.

## Optional Android edge worker

A restricted, non-root Android/Termux worker for small approved compute tasks is available. It uses the private application API and will not execute arbitrary CI scripts or access personal phone data. See [Android edge worker setup and safety](docs/ANDROID-EDGE-WORKER.md); it is not yet installed or verified on a physical phone.

## Installation in five minutes (after installing prerequisites)

1. Install Python 3.10+, Docker Desktop (Windows/macOS) or Docker Engine + Compose V2 (Linux), and the official `gh`, `glab` or `circleci` CLI for providers you use.
2. Extract the project into a **private local folder**, not a public cloud-sync directory.
3. Windows: double-click `start-windows.bat`; macOS/Linux: run `sh start-mac-linux.sh`.
4. Run `python hub.py dashboard` (macOS/Linux might require `python3`). The browser opens a **local owner session** on `127.0.0.1:8765`.
5. Click **Sign in** and **Register runner** for a provider, complete its official browser consent, then add its workflow template from `templates/` to a trusted project.

See [installation and verification](docs/INSTALL.md) for tested-command checklists and [security notes](docs/SECURITY.md) for risks before using personal hardware.

## Platform compatibility

| Provider | Browser-based CLI login | Runner provisioning | Repository and pipeline cockpit |
|---|---|---|---|
| GitHub.com | `gh` | Repo- or organisation-scoped Actions runner | Create/list repositories; list, retry, cancel runs |
| GitLab.com | `glab` | Project-scoped protected runner | Create/list projects; list, retry, cancel pipelines |
| CircleCI Cloud | `circleci` | Machine runner 3, resource class and agent token | Runner administration only; API cockpit is future work |

### Key commands

```bash
python hub.py doctor
python hub.py dashboard
python hub.py connect github
python hub.py setup gitlab
python hub.py setup circleci
python hub.py status
python hub.py logs gitlab
python hub.py mobile on                 # optional: private phone access
python -m unittest discover -s tests -v
```

The UI launches interactive CLI setup in a visible terminal on **the owner PC**. It cannot bypass required consent, account permissions or provider authentication. Self-managed GitLab requires suitable CLI authentication configuration and is not covered by live tests.

## Security model

- Binds HTTP only to IPv4 loopback (`127.0.0.1`), not the public internet.
- Desktop session uses an administrator secret exchanged for an HttpOnly SameSite cookie; local sign-out revokes the in-memory session. The private bootstrap secret persists locally and must be protected.
- Optional Tailscale Serve presents private HTTPS access; the owner must approve the identity and choose viewer or editor. A viewer cannot create repositories or modify pipelines. The **local PC owner** alone can start external provider sign-in, runner registration and device approvals.
- State-changing requests require same-origin checks, a custom header and session/role authorization. External CLI commands use fixed argument arrays, bounded output, timeouts and strict resource validation; arbitrary commands are not accepted.
- Audit log contains actor, operation, target and outcome, **never CLI output or account tokens**.
- Runner credentials still exist inside native runner storage and/or ignored `.local` state. Docker administrator rights are high privilege. **Never run unreviewed pull-request code on a personal computer.**
- A local process with access to your computer may be able to spoof private reverse-proxy headers. Use a dedicated worker PC/VM for hostile code and do not expose the dashboard through other proxies.

**No subscription guarantee:** self-hosting removes hosted execution-minute quotas for eligible native runners, but your hardware/electricity, storage, networking, and provider plan limits remain. CircleCI specifically still has plan-related limits.

## Open-source and project status

Open CI Hub is **MIT licensed** and its core is neutral: no account, organisation, company or domain is hard-coded. Contributions for Azure DevOps, Forgejo, Gitea, Jenkins, Buildkite and other providers are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md), [ROADMAP.md](docs/ROADMAP.md), and [SECURITY.md](docs/SECURITY.md).

The public source repository is [Raebu/open_ci_hub](https://github.com/Raebu/open_ci_hub). Never commit `.local/` or provider credentials.

### What remains before a verified production release

- Install the official CLIs and Docker, then perform a **real sign-in and runner registration on a controlled PC**.
- Complete at least one native build for GitHub, GitLab and CircleCI; verify job tags, protected refs, artifact behavior and teardown/deregistration.
- Obtain a security review, particularly of native runner isolation, reverse-proxy identity, token storage, updates, logs and device permissions.
- Optional true cloud-synced multi-computer identities require a **separately deployed control plane** and OIDC/OAuth infrastructure. Do not confuse the shipped Tailscale private workspace with an always-on cloud account.

## Runner storage and safety

The GitHub runner image includes a bootstrap that restores missing `config.sh` and `run.sh` into the persistent runner volume before registration or startup. Docker normally seeds **new named volumes** from existing image content; this bootstrap also protects against partially initialised volumes and avoids depending on Docker's one-time copy behaviour. Runner registration is then retained across container restarts. Updating the image does **not** update an already-populated volume's runner binaries; plan a controlled runner upgrade and re-registration rather than deleting live volumes without first deregistering them.

**Untrusted code warning:** self-hosted runner jobs run code on your computer. Do not attach this runner to public-repository pull requests from unknown contributors. GitHub and GitLab platform permissions, protected branches and operator approval are required; the project does not provide a general-purpose sandbox. Never mount the host Docker socket into the runner container to make builds work.

## Local validation and native readiness

```sh
python3 scripts/validate.py
python3 scripts/validate.py --containers
python3 hub.py verify-provider github OWNER/REPOSITORY
python3 hub.py verify-provider gitlab GROUP/PROJECT --branch main
```

The dashboard's **Check native runner** action queries provider readiness without
starting a job. GitHub/GitLab workflow application rechecks eligibility and refuses
when it cannot verify an online native worker. GitLab additionally requires a
protected target branch. CircleCI remains explicitly unverified. Container uptime
alone is not evidence that a provider can schedule a job.

## Native push-to-run workflows (new)

Open CI Hub has a **Native CI onboarding** screen. Select an existing repository,
preview a new push-triggered workflow configured for your native GitHub Actions,
GitLab CI/CD, or CircleCI runner, and explicitly approve the creation of a
provider pull/merge request. Existing workflow files are never overwritten.
GitLab also supports project **and group** runner registration, including adding
another runner to the existing GitLab runner container. See
[Native CI setup](docs/NATIVE-CI.md) before enabling it on a public repository.

**This does not silently migrate existing paid hosted CI jobs.** Existing
workflows must be reviewed and redirected to eligible self-hosted runners to
avoid their hosted minutes. Live provider jobs and safe execution of untrusted
code remain release requirements; the project is still a community preview.

## Autonomous repository fleets (opt-in)

Open CI Hub includes scoped, repeatable migration controllers for existing CI jobs rather than only adding smoke tests:

- **GitHub**: [GitHub fleet guide](docs/FLEET.md). Scan an account/organisation and prepare review pull requests that move compatible default-branch jobs onto eligible native self-hosted runners while preserving build steps.
- **GitLab**: [GitLab fleet guide](docs/GITLAB-FLEET.md). Scan authorised groups/subgroups and prepare review merge requests for conventional protected default-branch jobs using native group/project runner tags.
- **GitLab personal namespaces**: Use `python3 gitlab_fleet.py scan-personal USERNAME` to discover repositories owned by the authenticated user; separate scope approval is required for review-only migration. See the [GitLab fleet guide](docs/GITLAB-FLEET.md).

Use the one-time scope approval and optional hourly user timers on your own computer. Both controllers are **disabled by default**, do not automatically merge workflow changes and block ambiguous/untrusted pipelines. A personal GitHub account may need separately registered per-repository workers; eligible organisation/group runners can serve multiple authorised repositories. CircleCI continues to support native workflow onboarding and readiness checks, but complex existing CircleCI workflow migration is not automated or live verified.

**Do not confuse automated migration proposals with universal guaranteed job compatibility or production certification.** Review worker isolation, toolchains, permissions and all pre-existing hosted workflow triggers before merging proposals.

### Personal GitHub repository runner pool

If your GitHub repositories are under a personal account rather than an organisation, a single repository runner does **not** have access to all your other repositories. The optional [per-repository runner pool](docs/RUNNER-POOL.md) registers separate native GitHub runners with isolated persistent Docker volumes after a second, explicit scope approval.

It caps simultaneous worker processes, looks for queued provider jobs, and can rotate idle workers on a five-minute user timer. The pool never registers untrusted PR-triggered workflows, replaces existing credentials or deletes registered volumes without approval. It needs Docker and access to the native GitHub provider; live pool registration and rotation are **not yet externally verified**.

### CircleCI existing workflow migration

For GitHub-backed CircleCI projects with an authorised native machine resource class, the [CircleCI fleet guide](docs/CIRCLECI-FLEET.md) documents a disabled-by-default migration controller. It preserves conventional existing machine-job build steps, proposes resource-class changes as GitHub PRs, and refuses unsafe or ambiguous configurations. Unlike the verified GitHub native push runner, real CircleCI self-hosted execution remains unverified until an eligible CircleCI instance and live job are available.

## Secure Garuda Linux host agent (optional)

The [host agent guide](docs/HOST-AGENT.md) describes a separate, **non-root** local management service for your Linux PC. It can show host status, install explicitly approved Flatpak desktop apps for your desktop user, and start/stop explicitly approved systemd user services. An owner session or separately authorised Tailscale mobile editor uses the existing dashboard's **My PC** section, two-step confirmation and asynchronous job outcome polling.

**No CI/CD job is given direct administrator access or a host-agent socket.** The agent never listens on a TCP port or runs arbitrary shell commands and does not perform root-level pacman installs. This is opt-in and requires one-time installation on the actual PC; a successful CI test does not activate it.

## Execution proof (not just a green tick)

Open CI Hub now distinguishes native provider pipeline statuses from [runner-reported CI execution receipts](docs/EXECUTION-EVIDENCE.md). Its own self-hosted Python build records the command, actual process exit, observed number of unittest tests, duration and hashed transcript, and stores a short-retention artifact for later comparison. Use **Pipelines → Jobs, logs & artifacts** to inspect the runner assigned, the provider's native job steps and whether receipts are present. Missing runner assignments and exhausted hosted quotas remain explicitly **not proven**. Neither a green status nor a self-hashed transcript is independent attestation.

## Explicitly authorised Host Terminal

A separate **Terminal** tab can create an actor-scoped interactive Linux pseudo-terminal on the real host, under the regular user account. It is a new shell, **not a mirror of an existing KDE Konsole window**. It is disabled until the host owner explicitly runs the enablement command and grants each mobile editor separately. See [Host Terminal security and activation](docs/HOST-TERMINAL.md). No CI runner receives arbitrary host console privileges, and a successful PR run does not imply the host agent is deployed on the PC.

## Personal WhatsApp — writing and handoff

The authenticated dashboard now includes a **Personal WhatsApp** composer: edit a draft, create a tone-specific writing suggestion without erasing your work, copy the text, and open the recipient's WhatsApp conversation with the message prefilled. **Only pressing Send inside WhatsApp actually sends the message.** This is not an unofficial personal WhatsApp API and does not download or store your WhatsApp conversations or contacts. Normal ChatGPT can also prepare a personal message through the direct-PC connector and open it for final review once that connector is deployed. See [supported personal WhatsApp workflow](docs/WHATSAPP-PERSONAL.md).

## Opt-in personal WhatsApp terminal (unofficial)

The optional [personal WhatsApp terminal companion](integrations/whatsapp-personal/README.md) implements the user's proposed `whatsapp-web.js` / `LocalAuth` / QR integration. It can read incoming individual WhatsApp Web messages and send manual replies; opt-in automatic replies use the user's privately approved writing samples and the existing local Ollama model. Unknown contacts, groups, sensitive conversations and excessive reply volumes are excluded. **No account is paired and no automatic sending is enabled by this code.** The library is unofficial, and using it for automatic messages may lead to personal WhatsApp account restrictions. It is intentionally separate from the CI worker and deployment subsystem.

## Controlled CI internet access

See [CI Network](docs/CI-NETWORK.md) for owner-approved public-Web HTTPS/HTTP access in local CI while retaining Docker network isolation, private LAN denial and existing resource limits. The dashboard's CI Internet view shows effective repository policies.

## Governed deployment engine (staging foundation)

[Deployment engine and multi-target roadmap](docs/DEPLOYMENT-ENGINE.md) (tracked in [deployment issue #49](https://github.com/Raebu/open_ci_hub/issues/49)) covers environment-locked release promotion, signed-artifact checks, local staging health/rollback and an audit record built on the existing `release_manager.py`. The [Raeburn Talent deployment profile](docs/ATS-DEPLOYMENT-PROFILE.md) documents ATS-specific application gates without embedding them in CI core. The source includes *opt-in* Cloudflare Worker version, Vercel immutable alias and SSH/Compose staging adapters plus a credential store and read-only deployment dashboard. None are enabled without an owner-installed private policy, signed target metadata and scoped secrets. Automatic production remains disabled pending independent CI attestation, provider rollback verification, environment isolation and human-approved policy.

## Signed deployment and rollback (optional)

[Signed deployments guide](docs/SIGNED-DEPLOYMENTS.md) explains immutable staged .tar.gz releases approved with an **offline OpenSSH signing key**, source commit and mandatory test/build/database check metadata. The unprivileged release manager validates SHA-256 and signature, rejects malicious archive members, verifies release files again before activation, atomically switches the user-service current pointer and health-checks the release. If the service fails to restart or reports unhealthy, the last pointer is restored; explicit rollback is also supported.

This is a host-local user-service deployment primitive, **not** cross-PC zero-downtime deployment, database rollback or independently attested CI. No signed release can be created by mere green status; a separate reviewer holding the private key must approve the release after evaluating evidence.

## Private multi-PC navigation

The mobile dashboard can now show an explicitly approved [Tailscale multi-PC inventory](docs/MULTI-PC.md), inspect each remote dashboard's HTTPS /health through certificate validation and open that machine's *own* private Open CI Hub dashboard. Devices, CI runners, host terminals and deployment approvals remain independent on each PC. **This is private navigation/health discovery**, not yet automatic distributed build scheduling or central cross-machine terminal execution.

## Reproducible signed source distribution

Use `python3 scripts/build_distribution.py HEAD` to produce deterministic Git-commit-pinned source archives and a SHA-256 digest. An owner may sign them separately with the existing offline `scripts/sign-distribution.sh` script. Recipients can verify the exact bytes and signer identity with `python3 scripts/verify_distribution.py ARCHIVE ARCHIVE.sig SIGNER APPROVED_PUBLIC_SIGNERS`. Automated CI has no release signing key and never claims an unsigned archive is signed. See [signed deployments](docs/SIGNED-DEPLOYMENTS.md).

## Private application runtime API (Phase 1)

Open CI Hub now includes a [private application API](docs/APPLICATION-RUNTIME-API.md) for your own services to submit typed JSON work to a bounded, durable SQLite queue. App-scoped submitter and worker tokens are separate and issued only by a host-owner CLI, never through browser JavaScript. Worker leases, heartbeats, bounded retries, idempotency keys and status/results are supported. Other applications can integrate through the included Python standard-library application_client.py. An **App Runtime** tab shows worker queue metrics without exposing secrets.

**Important:** The hub queues authorised work; separate application workers execute their own application logic under their own deployment/security boundary. This phase does not run untrusted commands, offer root access, install app containers, expose a public API, dispatch GPU tasks automatically, or activate remote services on Garuda. Full live integration requires host installation and protected Tailscale Serve configuration.

## Parallel workloads with RAM/VRAM safeguards

The [parallel resource scheduler](docs/PARALLEL-RESOURCE-SCHEDULER.md) coordinates app workers across multiple projects using per-project and global parallelism limits, conservative RAM and NVIDIA VRAM reservations, hardware telemetry and fail-closed admission. An opt-in managed worker runs owner-approved handlers under systemd user cgroups with MemoryMax/MemorySwapMax limits and a VRAM headroom watchdog. Worker leases that expire remain reserved as stranded until the owner verifies process termination. View current capacity in the mobile **App Runtime** tab. VRAM admission/watchdog are *not hardware-enforced VRAM quotas*, and existing CI runner workloads are not yet included.

### Intel Iris Xe on 16 GB Garuda

On detected Tiger Lake Intel Iris Xe hardware, GPU working-memory reservations share the 16 GB system RAM rather than using an imaginary NVIDIA VRAM pool. The scheduler starts with 4 GiB reserved for KDE/OS, three app jobs at most, and one integrated-GPU job; explicit host-owner settings take precedence. The mobile App Runtime shows shared RAM instead of misleading dedicated VRAM values. Real hardware and worker cgroup behaviour must still be verified after local installation. See [parallel scheduler Intel guidance](docs/PARALLEL-RESOURCE-SCHEDULER.md#intel-iris-xe-razer-book-13-16-gb-system-ram).

## Unified resource and operating controls

The private [Workload Control](docs/WORKLOAD-CONTROL.md) layer observes the Docker memory reservations for managed native GitHub/GitLab/CircleCI runners and GitHub personal runner pool, and deducts them before admitting more application jobs. CPU-limited native runner starts use the same safety check. Managed app workers add bounded output capture, cgroup MemoryHigh/MemoryMax and CPUQuota. Host memory pressure, thermal conditions and battery can reduce new-job concurrency. The mobile App Runtime provides pause/resume, Quiet/Balanced mode, queue priority and app-only emergency-stop controls. Live Garuda enforcement remains pending owner host activation and validation.

**Worker output hardening:** Worker responses are bounded independently from file creation. Abnormal scope termination requires verified systemd inactive state before releasing reservations. See [WORKLOAD-CONTROL](docs/WORKLOAD-CONTROL.md).


### Guarded CI provider containers

The existing GitHub/GitLab/CircleCI Compose runner templates no longer automatically restart Docker containers outside central admission checks. A separate opt-in, owner-approved guard verifies preserved existing Docker container identity, RAM/swap/CPU/PID/security caps, Docker restart=no, and laptop pressure policy before starting an idle provider runner. A disabled-by-default user timer can retry approved starts after reboot. No containers or named volumes are created/deleted/migrated automatically. See [provider runner admission](docs/PROVIDER-RUNNER-ADMISSION.md). Native provider jobs remain capped by Docker; this is not complete control over scheduling inside each external CI provider.
