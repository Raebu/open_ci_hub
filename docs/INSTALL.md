> **v4 community preview update:** The graphical dashboard now has Sign in and Register runner buttons, repository inventory, GitHub/GitLab pipeline cockpit (list/retry/cancel), system diagnostics, local audit log and desktop sign-out. All interactive provider CLI sign-in and registration still happen in a terminal on the owner PC. For full v4 verification steps see [VERIFY.md](VERIFY.md). Some older notes below describe the v3 foundation; the v4 README and security guide take precedence where the UI has changed.

# Install Open CI Hub on your own computer

## Requirements

- Windows 10/11 with WSL2 and Docker Desktop, macOS with Docker Desktop, or Linux with Docker Engine + Compose V2.
- Python 3.10+ (`python`, `python3`, or `py -3`).
- Current official `gh`, `glab` and/or `circleci` CLIs, one for each provider you enable. You do **not** need to install the other providers' CLIs.
- Outbound HTTPS for the cloud services/registries. No inbound port forwarding required.
- For small jobs, at least 4 GB available RAM and 10 GB disk; more for builds.

Official CLI install sources: [GitHub CLI](https://cli.github.com/), [GitLab CLI](https://gitlab.com/gitlab-org/cli), [CircleCI CLI](https://circleci.com/docs/local-cli/). CircleCI browser sign-in requires a current CLI build that supports `circleci auth login`.

## Start

1. Extract the ZIP into a directory only you can access, preferably **not** a cloud-synced location.
2. Launch Docker Desktop/Engine, then verify `docker info` and `docker compose version`.
3. On Windows, double-click `start-windows.bat`. On macOS/Linux, run `sh start-mac-linux.sh`.
4. Select option 1 (diagnostics), then option 2, 3 or 4 (register an official provider runner).
5. Browser sign-in will appear if no existing CLI account login is detected. You grant access to the official CLI, **not this open-source project**.
6. Enter a non-secret repository/project URL or resource class when requested. Runner credentials are generated automatically, subject to your permissions.

Alternative commands:

```bash
python hub.py connect github      # login with GitHub only
python hub.py setup github        # login if needed; add runner
python hub.py setup gitlab        # login if needed; add runner
python hub.py setup circleci      # login if needed; add runner
python hub.py dashboard
```

On macOS/Linux substitute `python3` if necessary.

## GitHub Actions

- Choose a **trusted private repository** such as `https://github.com/OWNER/REPOSITORY`. Org scope is possible but broader and requires organisation administration permission, so repo scope is safer.
- Authenticate with your browser through `gh` if not yet signed in. The hub requests a short-lived registration token from GitHub's API, registers the official worker and starts the container.
- Add `templates/github/open-ci.yml` to `.github/workflows/open-ci.yml` in your app repo. The workflow uses `runs-on: [self-hosted, linux, local-ci]`.
- For organisation runners, you may need `gh auth refresh -s admin:org` to authorise org runner management. The hub does not obtain broad org permissions silently.
- Jobs requiring Docker containers or Docker builds need a separate worker design; no host Docker socket is exposed here.

## GitLab CI/CD

- Use the full project URL, e.g. `https://gitlab.com/group/myproject` or `https://gitlab.example.org/group/project` (self-hosted GitLab must have official `glab` CLI OAuth configured).
- Browser-authorise the `glab` CLI. You need **Maintainer or Owner** permissions.
- Hub calls the GitLab project lookup and `POST /user/runners` to create a **project-scoped runner** with tag `local-ci`, `run_untagged=false`, and `access_level=ref_protected`.
- The returned `glrt-` authentication token is used only for official runner registration; it is then held in the runner's Docker volume (not the Hub's own files).
- Add/merge `templates/gitlab/.gitlab-ci.yml` into your application's `.gitlab-ci.yml`, and make sure the branch is protected (otherwise the job may remain pending).
- A tagged, protected job runs on your computer instead of GitLab.com shared runners. Jobs without that tag may still be scheduled on GitLab.com shared runners.

## CircleCI

- Install a **current official CircleCI CLI** and sign in through its browser-based OAuth flow.
- Provide your resource class in `namespace/class` form. The setup can optionally create a new class (you must give your CircleCI organisation slug) or use an existing class.
- Hub automatically generates a separate runner token using `circleci runner token create`, then writes it to local, ignored `.local/circleci.env` for agent restarts. No manual token pasting.
- Edit `templates/circleci/config.yml` with your class and add it to your app repository as `.circleci/config.yml`.
- CircleCI runner terms and plan credits, data transfer and storage quotas still apply. Admin permissions may be required to create resource classes.

## Inspect and manage

```bash
python hub.py doctor
python hub.py status
python hub.py logs gitlab
python hub.py stop gitlab
python hub.py start gitlab
python hub.py dashboard   # localhost:8765, interactive
```

## Important troubleshooting

- **`gh` / `glab` / `circleci` missing**: install the official CLI for that provider, then restart the terminal.
- **Browser login failed**: try `python hub.py connect <provider>` again, check permission/scopes, and make sure the CLI version supports browser login. GitLab self-managed may need an administrator-configured OAuth application client ID.
- **GitHub registration forbidden**: repository admin permissions required; org runners need org admin and `admin:org` OAuth scope.
- **GitLab runner creation forbidden**: ensure you have Maintainer/Owner access, token is authorised for API operations, and runner creation is permitted by instance policy.
- **CircleCI class/token creation forbidden**: ensure the right organisation is used and you have runner administration rights.
- **GitLab job stays pending**: protect the relevant branch, use tag `local-ci` in every matching job, and check the runner status.
- **Build needs Docker**: this limited starter intentionally does not give untrusted CI commands control of the host's Docker daemon. Use a dedicated isolated VM/worker instead.
- **Computer sleeps**: workers go offline. Keep the computer awake only when needed.
- **Remove a runner**: first stop the agent; deregister it on provider's website (and revoke its token where necessary); then remove containers and volumes. Merely deleting files does **not** revoke a runner credential.

The project was unit-tested locally, but you must verify browser login, native runner image compatibility, registration and workflow execution on your own system before trusting it with code or deployments.

## Graphical dashboard (v4 updates)

Run `python hub.py dashboard`. It opens `http://127.0.0.1:8765` with a one-time-in-browser local administrative session link. Keep that link private: it contains an administrator secret in the URL fragment. The fragment is removed from browser history after being exchanged for a secure, HTTP-only, 12-hour, same-site session cookie. The server binds to localhost only.

The dashboard includes:

- **Sign in** for GitHub, GitLab or CircleCI: opens the **official provider CLI** in a *visible terminal on your PC* and launches its browser authorisation. Some CLIs still ask interactive questions; those are intentionally not bypassed. The PC must have a desktop terminal and installed provider CLI. Sign-in is not started remotely on a phone.
- **Create repository**: GitHub/GitLab, private or public. Public requires an extra acknowledgement and confirmation. Requires existing provider CLI login with sufficient permissions. CircleCI is a CI provider rather than a Git repository host, so it does not offer repository creation.
- **Runner start/stop** for providers that have already completed runner registration using `python hub.py setup <provider>`.
- **Approve/revoke mobile identities** after they ask for access through private Tailscale Serve; choose viewer (default) or editor.

If sign-in cannot launch a terminal in your desktop environment, run `python hub.py connect github` / `gitlab` / `circleci` directly in your terminal.

## Phone and multi-device access without a cloud server

Open CI Hub can keep one shared *live* workspace on your own computer and make it available privately from mobile. It uses **Tailscale** for an existing unified identity and an encrypted HTTPS connection; users do not need separate Open CI Hub passwords. This does **not** turn Open CI Hub into a hosted cloud service, synchronize data across unrelated installations, or keep the dashboard available when your PC is off.

1. Install [Tailscale](https://tailscale.com/download) on your PC and phone.
2. Sign into both devices with the **same tailnet**, using your chosen Tailscale identity provider. GitHub, Google, Apple and Microsoft are supported directly. Other OIDC sign-in providers depend on tailnet administrator configuration.
3. Start `python hub.py dashboard` on the PC and leave it running. Docker jobs also require Docker to be running.
4. On the PC run `python hub.py mobile on`. This calls `tailscale serve --bg 8765` to publish the localhost dashboard to **your tailnet only** over HTTPS. Tailscale may prompt you to activate HTTPS.
5. Run `tailscale serve status` to obtain the `https://...ts.net` private address. Open it on your phone while Tailscale is connected.
6. Tap **Request device approval** on the phone. On the PC refresh the dashboard, find the Tailscale account identity under **Mobile & other devices**, and tap **Approve**. Refresh the phone; the same live dashboard is now available there.
7. Use **Revoke** on the desktop dashboard to remove a mobile user's access. To stop sharing, run `python hub.py mobile off`, which disables the HTTPS port 443 Serve mapping (check for any other services using that mapping).

**Security**: This works only through private **Tailscale Serve**, not Funnel; do not forward port 8765 on your router, bind the local app to `0.0.0.0`, or expose it through an arbitrary unauthenticated reverse proxy. The hub requires Tailscale's user identity headers and matches approved users on each request; public Funnel traffic has no trusted identity headers. Mobile users are approved as read-only viewers by default; the PC owner can explicitly grant an editor role with runner controls and repository creation. Read `docs/SECURITY.md` for limitations. For hosting the control plane independently of your PC, future development would need a separate deployed service, persistent storage, federated OAuth/OIDC and a runner-to-cloud connection.

### Setup terminal closes before you can read the result

On Linux, the dashboard's interactive setup launches a separate terminal, which traditionally closes as soon as the child command exits. The updated launcher now waits for you to press Enter and clearly displays **Completed successfully** or **FAILED**. You may also run `python3 hub.py setup gitlab` directly in your Fish terminal to retain output, but **first** inspect `python3 hub.py status`, `docker compose --profile gitlab ps -a`, and the runner configuration; do not blindly repeat setup, because an attempted registration may have already created a server-side GitLab runner.

## Native push-to-run setup
Open **Native CI onboarding** in the dashboard after a runner has been registered and verified. Choose provider, authorised repository and default branch; preview the self-hosted workflow and explicitly approve the provider pull/merge request. Merge it after checking trust and protection. Existing CI workflows are never overwritten, and existing hosted jobs must be migrated separately. See [NATIVE-CI.md](NATIVE-CI.md).
