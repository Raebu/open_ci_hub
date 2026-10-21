# Open CI Hub: streamlined ChatGPT + autonomous CI

## Architecture

ChatGPT with GitHub and Open CI Hub connections handles interactive development, tests, reviews and approved deployments. It does not need another always-on coding-agent process.

Unattended CI has two user-level services:
- open-ci-hub-bridge-poll.service uses the authenticated gh/glab CLIs to check locally allowlisted GitHub/GitLab branches every three minutes, with persistent exact-SHA cursors; its first observation only establishes a baseline.
- open-ci-hub-bridge-worker.service processes queued exact-SHA jobs through the existing Open CI Hub broker. Two worker threads can run concurrently when the broker permits; its memory, thermal, native-runner and project limits always win.

There is no second generic runner scheduler. The old CI and native runners continue working unchanged.

## Install on Garuda as desktop user

First create the private .local/ci-bridge/manifest.json (0600) using the schema in HUB-OWNED-CI-BRIDGE.md. Only enrol a repository after reviewing its image and argv test steps. The build image must already be locally available by its pinned digest. Never put credentials in source code or the manifest.

1. python3 ci_bridge.py init-state
2. python3 ci_bridge_poll.py once
3. bash scripts/install-ci-bridge-services.sh
4. systemctl --user is-active open-ci-hub-bridge-poll.service open-ci-hub-bridge-worker.service
5. python3 ci_bridge.py status
6. python3 app_runtime.py resources
7. journalctl --user -u open-ci-hub-bridge-poll.service -n 30 --no-pager
8. journalctl --user -u open-ci-hub-bridge-worker.service -n 30 --no-pager

Service installation never changes the legacy one-build CI limit, stops native runner containers, modifies Docker volumes, or replaces the secure ChatGPT tunnel.

## Pilot and limits (21 October 2026)

The first private manifest allows only Raebu/open_ci_hub main, using the preloaded digest-pinned Python 3.12 image and CI bridge test modules. The status context is labelled Open CI Hub/bridge-smoke because this check is NOT the full CI suite.

GitLab provider access, branch querying and signed webhook parsing are implemented and tested; a production GitLab repository must first have its own meaningful, offline-capable test commands and image. The private Talent application requires Node/PHP tests, not a no-op Python syntax check.

Provider polling checks the newest branch SHA only. Multiple pushes between polls can be coalesced; PR/MR activity and every-push replay require separate authentic signed webhooks. The new worker status-reporting path has not yet been verified against a real build. Do not treat it as a release gate before that verification.

The provider event never controls shell commands: only owner-reviewed 0600 manifest steps are executed. Checkout verifies the full SHA. Builds use a pinned image with no network, no mounted host Docker socket or provider token, bounded RAM/CPU/PIDs, and resource-aware admission.

## Opt-in CI internet

The private owner manifest can enable controlled Full public-Web mode for trusted protected-branch pushes; Offline remains default. The container keeps network-none and gains a private public-only proxy. See [CI network guide](CI-NETWORK.md) for actual Docker security tests and caveats.

## Resource safety

During installation, the CPU reached 100 degrees C and the Intel laptop experienced memory/swap pressure. Open CI Hub was correctly denying new jobs for critical temperature and memory reserve. Do not loosen these policies to make a job run. A reversible power-saver profile lowered observed idle temperatures considerably.

Leave live browser sessions, Supabase volumes, business application containers and other CI services alone unless deliberately managing their shutdown.

## Safe rollback and inspection

Check the queue with python3 ci_bridge.py status and inspect resource blocks with python3 app_runtime.py resources.

Stop only the new bridge services:
systemctl --user stop open-ci-hub-bridge-poll.service open-ci-hub-bridge-worker.service

Disable future start:
systemctl --user disable open-ci-hub-bridge-poll.service open-ci-hub-bridge-worker.service

Restart:
systemctl --user enable --now open-ci-hub-bridge-poll.service open-ci-hub-bridge-worker.service

Do not delete .local state/logs or untracked Service_Business/, and never run git clean -fd as a maintenance shortcut.

## Remaining release gates

Real GitHub build and exact-commit status proof; GitLab project-specific manifest and full validation; restart/retry/lease-loss and cancellation verification; dashboard log/queue view; GitHub/GitLab webhook authenticity and replay protection for lossless operation; independent isolation/credential review before broad unattended use.
