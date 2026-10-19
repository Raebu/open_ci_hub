# Native GitHub personal-account runner pool (opt-in)

GitHub personal-account repositories usually cannot share a single organisation runner. The Open CI Hub runner pool provides **separate native runner registrations per authorised repository** without manually running Docker registration each time.

This is an optional Linux/Docker feature and does not replace GitHub Actions. GitHub still creates, queues and schedules each job natively.

## Explicit approval and installation on your own computer

The pool does not have permission to register runners merely because the user authorised GitHub fleet review PRs. Runner provisioning requires a second one-time scope approval:

~~~fish
cd /path/to/open-ci-hub
docker compose --profile github build github
python3 runner_pool.py authorize Raebu --confirm
python3 runner_pool.py scan Raebu
python3 runner_pool.py sync Raebu --apply
~~~

It handles only repositories with verified **default-branch-only** GitHub workflows already routed to the self-hosted Linux local-ci label. Workflows with pull_request, ambiguous events, other hosted runners, reusable jobs, custom executor services or unsafe sources are rejected. No global bulk execution is enabled by default.

Each repository gets a deterministic isolated named volume and Docker container, its own GitHub registration and a native runner identity. No host Docker socket is mounted. Registration tokens are temporary and passed as short-lived process environment variables, never written into the repo or local metadata. Docker administrators can inspect active containers; this is not a sandbox suitable for untrusted public code.

The pool never replaces existing runner registrations or deletes their state volumes. If registration fails after creating a volume, the tool **stops safely** and requires reconciliation before retrying.

## Resource management

A maximum of **three concurrently running pool workers** is enforced. Each uses at most 2 GB RAM, 2 CPUs and 256 processes. Up to four new repository registrations may occur per sync pass.

When another repository has queued GitHub Actions jobs, the pool can stop an idle worker (only after checking its associated repository has no in-progress or queued workflow runs) and start the eligible waiting repository's worker. If remote activity cannot be checked, the active worker is not stopped.

The scheduler runs only when invoked. To check queued jobs and rotate workers automatically, install the five-minute user timer:

~~~fish
bash scripts/install-runner-pool-service.sh
systemctl --user enable --now open-ci-hub-pool@Raebu.timer
systemctl --user list-timers 'open-ci-hub-pool*'
journalctl --user -u open-ci-hub-pool@Raebu.service -n 40
~~~

Disable it without removing registrations:

~~~fish
systemctl --user disable --now open-ci-hub-pool@Raebu.timer
~~~

Native runners need your PC powered on, Docker running and access to GitHub to execute jobs. Workers may take several minutes to become available, and provider queue expiration limits apply. Persistent worker environments still need the required language toolchains and dependency isolation. A test that passed on a hosted VM is not automatically compatible with this Docker image.

The pool currently operates only for GitHub. GitLab group runners already serve multiple group projects; CircleCI resources use their own resource classes. Actual real-host Docker provisioning, queue rotation and failures must be validated independently before declaring the pool production-ready.

## Safety controls and troubleshooting

- No approved scope: refuse registration.
- No trusted main-only native workflow: skip project.
- Existing untracked named volume: refuse to overwrite.
- Existing registered metadata with missing runner configuration: refuse to re-register.
- Pool full: jobs stay queued pending an eligible worker.
- Failed remote API: never stop a running worker on uncertain activity.
- Stop: python3 runner_pool.py stop OWNER/REPO (retains metadata, tokens in native state and volume).

This deliberately does not provide a public pull-request execution sandbox. Use ephemeral hardened workers and independent security review before accepting arbitrary third-party PR code.
