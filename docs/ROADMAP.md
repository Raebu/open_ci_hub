# Roadmap — Open CI Hub

## Delivered in v4 source

- Native runner integration for GitHub, GitLab and CircleCI with official CLI browser sign-in and generated runner credentials (live validation pending).
- Responsive local dashboard: sign-in, register runner, start/stop, create and list GitHub/GitLab repos, list runs/pipelines, explicit retry/cancel, diagnostics and SQLite audit log.
- Tailscale Serve access to the same instance, identity approval/revocation, viewer/editor and desktop-owner boundaries.
- 57 automated Python tests with negative API/CLI input tests, and JavaScript syntax validation.

## Production release blockers (highest priority)

1. **Real-machine three-provider end-to-end suite:** native runner registration, successful trusted CI job, restart, cancellation, retry, and provider deregistration.
2. **Security audit and runner isolation:** verify images, run-as users and persistence; restrict workspaces and protect personal hosts; threat model the Tailscale Serve identity boundary.
3. **Cross-platform installation QA:** Windows 11 + WSL2, macOS Intel/Apple silicon and supported Linux distros; package signed native installers.
4. **Credential lifecycle:** rotate local owner secret, revoke runner credentials through provider APIs, backup/restore audit/config metadata safely, verify failure recovery.
5. **API contract tests:** integrate official CLI responses in an isolated test environment, test provider pagination, disabled permissions, quotas, subgroups and error formats.

## Community extension tracks

- CircleCI repository/job cockpit, additional native providers (Azure DevOps, Bitbucket, Forgejo/Gitea, Jenkins, Buildkite, Woodpecker).
- Guided workflow editor with validated recipes, preview of changes and explicit confirmation before writing into connected repositories.
- Multiple worker PCs, pool scheduling and ephemeral VM workers. An agent must never expose arbitrary remote shell or Docker command execution.
- Optional hosted control plane with OpenID Connect, PKCE, persistent encrypted tenant-isolated storage and outbound-only agents. PC-offline mobile mode should show cached history but no fictional job execution.
- Workspace quotas, usage summaries, alerts, retention controls, provider-specific artifact/trace viewing with scrubbed secrets, policy-as-code and production environment approvals.
- Releases with SBOM, signed artifacts, cross-platform QA, well-defined compatibility matrix and security advisories.

All future functions remain non-shipped until tested and verified. The term “provider-agnostic” means pluggable adapters, not interchangeable provider CI semantics.
