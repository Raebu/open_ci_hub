# Engineering delivery — 2026-10-19

Base: upstream `main`, merge commit `fa8d4db` (PR #3).
Verdict: **community preview; NOT production ready**. This delivery improves
native onboarding and recovery; it does not complete the entire product mandate.

## Implemented

- Read-only provider readiness endpoint, dashboard check and `verify-provider` CLI.
- GitHub: verify repository write access, Actions enabled, online repository-accessible
  runner and case-insensitive `self-hosted`, `linux`, `local-ci` labels before opening
  a workflow PR. Busy online runners can accept queued work. No hosted fallback.
- GitLab: verify target branch protection and an online, unpaused, protected project
  or group runner with `local-ci`. Query scopes separately so shared runners cannot
  hide the native workers. Shared/instance runners never qualify.
- Recheck readiness on apply; a preview alone cannot authorize or bypass the gate.
- Report CircleCI readiness as UNVERIFIED. Login alone is not a runner or push test.
- Cross-process setup lock protects native config and local metadata from concurrent
  registrations. OS releases the lock on process death; preserve its inode.
- Validate GitLab metadata before minting a credential or registering a worker.
- Remove GitHub `--replace`; refuse conflicts instead of invalidating an existing runner.
- Bound provider API commands (120 seconds), with longer interactive browser login
  allowance (600 seconds), and redact timeout errors.
- Retain GitLab custom HTTPS ports and send boolean/integer API fields using typed
  flags, per the official [glab API reference](https://docs.gitlab.com/cli/api/).
- Quote YAML branch scalars, reject invalid GitHub source paths, escape macOS
  terminal command strings and exclude all non-container files from Docker context.
- Add repeatable local validation with optional isolated container smoke checks.
  A bootstrap regression test no longer writes dummy registration metadata locally.

## Evidence actually collected

| Gate | Result |
|---|---|
| Baseline Python suite | 88 tests passed |
| Final Python/API/security suite | 116 tests passed |
| Python compilation, JavaScript syntax | PASS |
| All Compose profiles, without resolving secret env files | PASS |
| GitHub image build | PASS (cached base layers) |
| GitLab image build | PASS (cached base layers) |
| GitHub bootstrap and state retention | PASS, two isolated containers, private temporary named volume removed afterwards |
| GitHub authenticated repository runner inventory | PASS read access; zero registered runners for this source repository |
| Existing GitLab `gitlab-runner verify` | Command exited 0; no configuration changed |
| Existing GitLab native eligibility | PASS: project runner online, protected access, `local-ci`, target main protected |
| CircleCI CLI authentication and organisation discovery | PASS |
| CircleCI native resource classes and instances | Empty in connected organisation; push execution UNVERIFIED |
| Provider push builds, artifact retrieval, cancellation and restart recovery | UNVERIFIED |
| Phone access, Windows/macOS UI, independent security review | UNVERIFIED |

The existing authorised GitLab worker was inspected read-only. No registrations,
credentials, Docker service configuration or existing volumes were modified.
No provider CI jobs were triggered. All execution validation used local compute.

## Exact local verification and activation (Fish compatible)

After reviewing this PR, use its checkout or a normal clone containing the changes:

```fish
cd /path/to/open-ci-hub
python3 scripts/validate.py
python3 scripts/validate.py --containers
python3 hub.py doctor
python3 hub.py dashboard
```

The container option downloads missing images and builds locally. It creates and
removes only its own uniquely named validation volume; no provider job is started.
It leaves validation images available for inspection. Node and Docker Compose
supporting `--no-env-resolution` are required for the complete local check.

For an existing configured worker, inspect it before deciding to register anything:

```fish
python3 hub.py status
python3 hub.py verify-provider github OWNER/REPOSITORY
python3 hub.py verify-provider gitlab GROUP/PROJECT --branch main
docker exec open-ci-gitlab-runner gitlab-runner verify
```

For a **new, unconfigured** GitHub worker only:

```fish
python3 hub.py connect github
python3 hub.py setup github
python3 hub.py verify-provider github OWNER/REPOSITORY
```

For a **new, unconfigured** GitLab worker only:

```fish
python3 hub.py connect gitlab
python3 hub.py setup gitlab
python3 hub.py verify-provider gitlab GROUP/PROJECT --branch main
```

Protect the GitLab branch through the provider before the readiness check. Existing
working GitLab registrations should be retained, not repeated. Adding an authorised
project or group is available through the existing dashboard or `add-gitlab-project`
and `add-gitlab-group` commands. Runner storage is provider-native and persistent.

For CircleCI, an authorised organisation namespace/resource class and a connected
project must exist first. Use official account setup, then `python3 hub.py setup
circleci`. Never substitute another organisation's class. `verify-provider circleci
OWNER/REPOSITORY --resource-class NAMESPACE/CLASS` deliberately returns exit 2 until
end-to-end readiness can be established. The installed CLI was checked against
[CircleCI's CLI reference](https://cli.circleci.com/reference/).

Preview the native workflow in the dashboard, check readiness, then approve opening
the PR/MR. Review every existing workflow for hosted executors **before any push or
PR**, since existing workflows are retained and can have their own triggers. Merge
only after provider policies and runner trust are reviewed. The next approved push
schedules the native smoke job automatically. Record the run URL, worker identity,
logs, artifacts, retry/cancel results and restart recovery before marking any live
integration complete. Readiness checks do not schedule a test build.

## Outstanding release gates

- GitHub native registration and a real push job; CircleCI resource-class/project
  setup and real registration/job evidence. GitLab push-job evidence still needed.
- Full application build workflows and safely reviewed migration of existing CI;
  the wizard currently creates a smoke test and refuses to overwrite target files.
- Job-log/artifact cockpit, per-worker capacity/concurrency controls, managed safe
  upgrades/deregistration, ephemeral isolation and dependable recovery transactions.
- Full unified identity/device architecture, optional self-hosted cloud control plane,
  additional working provider adapters and cross-platform/mobile end-to-end tests.
- Dependency/container vulnerability assessment, immutable image/action pinning and
  independent security review. Image builds alone do not establish supply-chain safety.

The [GitHub runner API](https://docs.github.com/en/rest/actions/self-hosted-runners)
and [GitLab runner API](https://docs.gitlab.com/api/runners/) define the provider-native
checks used here. Runner inventory is a bounded point-in-time observation, not an
execution guarantee. GitHub checks the first 100 runners; GitLab checks the first
100 per project/group scope and fails closed if no eligible worker is observed.
