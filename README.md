# Open CI Hub

An **open-source, provider-neutral, local-first CI/CD control centre** for GitHub Actions, GitLab CI/CD and CircleCI. Use the providers' native self-hosted runners to build on a computer you control instead of purchasing hosted runner minutes.

**Release: v4 community preview; native onboarding is implemented in source and requires live verification.** Local Docker image builds/bootstrap and read-only provider checks have passed; real push builds across all three providers remain unverified. Do **not** treat it as a security-audited or production-certified service.

See the [engineering delivery and evidence](docs/ENGINEERING-2026-10-19.md) for
current validation, activation commands and remaining release gates.

## Highlights

- Responsive dashboard with **Sign in** and **Register runner** buttons. Authorization uses the **official provider CLIs and browser**, not copy/paste API keys.
- Native provider runner support: GitHub Actions, GitLab, CircleCI, with separate Docker Compose profiles. No host Docker socket or home-directory mount by default.
- Browse GitHub/GitLab repositories, create private or public repositories (with confirmation), inspect recent pipelines, and explicitly retry/cancel GitHub or GitLab runs.
- Control runners, inspect Docker/disk/CLI readiness, and view a **local SQLite management audit log**.
- Optional **mobile access through Tailscale Serve**, using a trusted tailnet identity with an owner-approved viewer or editor role.
- MIT licence, neutral branding, contribution policy, workflow templates, installation docs and tests.

**Important:** Mobile access uses the same live PC-backed workspace. It does **not** sync to a hosted account while the PC is offline. GitHub/GitLab/CircleCI require separate permissions. CircleCI pipeline browsing/retry is not yet implemented: its runner integration is available through the installer and runner controls. Provider-hosted storage/networking and plan limits may still apply.

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

Use the one-time scope approval and optional hourly user timers on your own computer. Both controllers are **disabled by default**, do not automatically merge workflow changes and block ambiguous/untrusted pipelines. A personal GitHub account may need separately registered per-repository workers; eligible organisation/group runners can serve multiple authorised repositories. CircleCI continues to support native workflow onboarding and readiness checks, but complex existing CircleCI workflow migration is not automated or live verified.

**Do not confuse automated migration proposals with universal guaranteed job compatibility or production certification.** Review worker isolation, toolchains, permissions and all pre-existing hosted workflow triggers before merging proposals.
