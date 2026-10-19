# Changelog

## Unreleased — native runner eligibility and setup safety

- Added provider-native readiness checks to dashboard, CLI and workflow application.
- Excluded GitLab shared runners and required protected branch/tag eligibility.
- Serialized runner setup across processes; validate metadata before registration.
- Removed automatic replacement of existing GitHub runners; bounded provider calls.
- Fixed GitLab typed API fields/custom ports and YAML branch scalar quoting.
- Restricted Docker build context; added isolated local container validation.
- Recorded real local and provider evidence, with remaining production gates explicit.

## 4.0.0 — 2026-10-19 (community preview)

- Added GitHub/GitLab repository inventory to the graphical dashboard, with provider navigation and responsive layout.
- Added GitHub/GitLab pipeline cockpit: recent runs, explicit retry and cancel, strict input validation, and authorization checks.
- Added local system diagnostics and SQLite audit log for sensitive management operations (no secrets recorded).
- Added runner-registration launcher button and sign-out on the owner PC; tightened mobile role controls.
- Added documented security boundaries, production-verification checklist, offline test workflow and 57 passing Python tests.
- Corrected roadmap and public release claims: *not* an always-on cloud identity service and *not* live-verified on native provider runners.

## 3.0.0 — Graphical workflows and private mobile access

- Interactive responsive dashboard: GitHub/GitLab/CircleCI sign-in buttons backed by official local CLIs and visible terminals.
- Repository creation on GitHub and GitLab with conservative visibility defaults and public confirmation.
- Runner start/stop from authenticated console, with access checks and CSRF/Origin protection.
- Local dashboard private session token, secure file storage and short-lived HttpOnly cookie.
- Tailscale Serve integration for unified multi-device identity, explicit local approval/revocation, no public listening socket.
- HTTP integration and repository validation tests; mobile/cloud hosted mode intentionally not claimed.

# Changelog

## 2.0.0 — Community-ready browser sign-in edition

- Neutral, provider-independent **Open CI Hub** project identity and install paths.
- Added modular provider adapters for GitHub.com, GitLab.com and CircleCI Cloud.
- Implemented official CLI browser sign-in instead of manual API-key entry.
- Implemented authorised automatic runner credential creation for GitHub repository/org, protected GitLab project, and CircleCI resource class.
- Added community, contributing, security and licensing documentation.
- Added 18 mocked, local unit tests for input validation, secure storage and provider API calls.
- No Docker daemon, provider CLI, real account login or live runner was available in the packaging environment; real CI job proof remains outstanding.

## 1.0.0 — Initial starter

- Local Docker Compose management of independent official runner agents.
- Read-only localhost dashboard and sample CI workflows.

## Unreleased — Native CI onboarding

- Add approved native push-workflow preview and GitHub pull request / GitLab merge request creation without overwriting existing configuration.
- Support CircleCI machine resource class workflow generation for GitHub-backed sources.
- Support group-scoped GitLab runners and multiple native GitLab runner registrations.
- Carry forward GitHub runner-state bootstrapping and durable setup-terminal failure output.
- Add security, plan and request-contract regression coverage. No hosted CI minutes are needed for local tests.
