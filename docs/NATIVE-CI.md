# Open CI Hub — native automatic CI on your own runners

**No hosted build minutes are required for jobs assigned to your self-hosted runners.** You still need provider CI configuration, authorisation, provider plan eligibility and an online computer. Provider artifact storage, network and other quotas may still apply. This is not an alternative hosted GitLab/GitHub build service.

## How dispatch works

Open CI Hub **does not intercept commits** or replace provider schedulers. On each push, GitHub Actions, GitLab CI/CD or CircleCI detects its own workflow configuration and dispatches the job to the native runner with the corresponding label/tag/resource class. Logs and check statuses remain in the provider. Open CI Hub locally manages the runner and provides a multi-provider dashboard.

| Provider | Target worker | How the push is routed | Status |
|---|---|---|---|
| GitHub Actions | Self-hosted GitHub runner | `runs-on: [self-hosted, linux, local-ci]` | Requires GitHub registration; the user-observed GitHub container was restarting because it was **not configured** |
| GitLab CI/CD | Project or group GitLab runner | `tags: [local-ci]` with protected branch | A GitLab runner was validated by `gitlab-runner verify`; full job still needs execution proof |
| CircleCI | Machine runner | `resource_class: namespace/class` | Sign-in established but live registration and job not yet verified |

**Never route untrusted contributions to a persistent self-hosted machine.** GitHub runner does not mount the host Docker socket by default. Use isolated ephemeral VMs/containers with separate credentials for untrusted code.

## Guided onboarding

1. Open the owner PC's dashboard using `python3 hub.py dashboard`.
2. Sign in to your provider with its official CLI. Register a project, organisation or GitLab group runner and confirm it is connected.
3. Under **Native CI onboarding**, choose GitHub, GitLab or CircleCI, select an authorised repository, and enter the default branch (usually `main`). For CircleCI use your actual resource class.
4. Click **Preview native workflow**. Read the generated YAML and safety warning before proceeding.
5. Click **Open review request** and confirm. This uses the provider's authenticated CLI and creates a new review branch and PR/MR. It **never automatically merges**.
6. Confirm runner eligibility and repository branch protection. Review and merge the PR/MR. A subsequent push to the configured default branch triggers the workflow natively and targets your self-hosted runner.

The first native job is an intentionally minimal **smoke test**, not an automatic migration of the repository's other CI jobs. Existing workflows can still target hosted executors and therefore can still consume hosted billable minutes. Do not claim otherwise. Migrating complex pipelines requires reviewing language/toolchain needs, operating system, permissions, caching, build containers and deployment secrets before changing `runs-on`, GitLab `tags`, or CircleCI `resource_class`.

### Existing workflow safeguard

- GitHub: creates `.github/workflows/open-ci-hub.yml` only if that pathname does not exist. Other workflows are untouched.
- GitLab: creates `.gitlab-ci.yml` only if absent. If the repository already has GitLab CI, GitLab's create-only endpoint refuses the change. Merge the self-hosted job into your existing configuration in a separately reviewed change.
- CircleCI: creates `.circleci/config.yml` through the connected **GitHub source** repository only if absent; CircleCI itself is not a repository host. Existing CircleCI configurations are never overwritten.
- GitHub onboarding and CircleCI-on-GitHub require `gh` login; GitLab onboarding requires `glab` login.
- Adding to a repository does not by itself enable provider CI services. Verify the repository's existing provider setup, permissions and branch protections before merging.

### Multi-project workers

- GitHub organisations can provide an organisation-scoped runner for eligible repositories. Other repositories require appropriately scoped native runner registration. Do not assume a personal GitHub runner covers all repositories.
- GitLab group runners serve projects under their authorised group. Choose **Register group runner** on fresh installs or **Add group** after existing project registration. The GitLab container can register multiple project/group runners into the same native `config.toml` file; local indexes contain no tokens.
- CircleCI runners accept jobs in their resource class, subject to provider plan rules. There is no independent cross-provider job dispatcher.
- You can run workers on multiple PCs by installing Open CI Hub on each machine and registering separate native runner identities. Centralised pool scheduling and offline sync are future work.

## Confirm real job execution (operator evidence required)

For GitLab after registration:

```sh
docker exec open-ci-gitlab-runner gitlab-runner verify
docker compose --profile gitlab logs --tail 60 gitlab
```

For GitHub after registration:

```sh
python3 hub.py status
docker logs --tail 60 open-ci-github-runner
```

A steady process is not proof of a registered/online runner. Confirm the native provider shows the runner online, merge one reviewed test workflow and verify the provider job finishes on the intended runner. Check cancellation, retry and recovery after Docker and PC restart. Do not delete volumes or repeat registration blindly when partial runner state exists. Registration creates server-side runner objects that may need explicit deregistration.

## Offline development tests, no provider-hosted minutes

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q hub.py operations.py onboarding.py web_console.py audit.py providers tests
node --check dashboard/app.js
docker compose --profile github config
docker compose --profile gitlab config
docker compose --profile github build github
```

These commands do **not** trigger GitHub Actions or GitLab CI pipelines. The Docker build does require Docker and potentially an external image download. The developer must run them on an appropriate machine before declaring the Docker environment verified.
