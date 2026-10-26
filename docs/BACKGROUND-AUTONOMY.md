# Open CI Hub — background autonomy, first operational slice

Status: implemented and tested on the Garuda host. Opt-in. NOT all ten
capabilities production-complete. It does not replace the existing runner,
host agent, CI bridges or user desktop.

## Human-first background browser

A fresh, headless Chromium instance runs with Playwright Core in an independent
transient systemd --user service. It does not use the KDE DISPLAY/WAYLAND_DISPLAY,
personal Chrome profile or personal browser cookies. It refuses downloads and
unapproved HTTPS origins. CPUQuota=60%, MemoryMax=768M, MemoryHigh=720M,
MemorySwapMax=0, IOWeight=15 and TasksMax=384 have been verified with a real
headless Chrome navigation/extraction/screenshot on Garuda. The earlier
TasksMax=96 limit was too restrictive and caused a real browser timeout.

The foreground resource gate admits new work only with 4GB remaining system
RAM, low system load and acceptable known temperature. This is conservative,
but a 30-minute real user-session latency benchmark is still outstanding.

The Chromium worker now also runs inside an unprivileged bubblewrap mount,
user, PID, IPC and UTS namespace. It can access only read-only system files,
the approved browser code, one temporary read-only mission file, private
ephemeral storage and a single job-artifact folder. Real namespace tests
confirm the human user's home directory is not visible. If bubblewrap is
missing the browser fails closed; it does not silently downgrade to an
unrestricted same-user process. The browser still shares the host network,
so malicious DNS rebinding, browser exploits and kernel attacks require a
separate network/VM security review before hostile sites can be trusted. Browser screenshots can contain site data and are
retained only in private job artifacts.

## Owner-approved task lifecycle

Plans are bounded JSON: mission ID; up to eight exact public DNS hosts; a cost
and time ceiling; and up to twenty steps with earlier-step dependencies.

Fixed actions: fetch (bounded HTTPS read-only GET, public DNS resolved once,
pinned to the verified IP and TLS hostname; no proxies or redirects), browse
(DOM extraction and screenshot; click/fill require additional owner consent),
digest (SHA-256), inspect (canonical JSON validation/digest). No arbitrary shell,
page-provided instructions, personal files, root, provider account creation,
payment, deployment or external message sending can be authorised by a page.

The private SQLite ledger holds task state and SHA-256 evidence receipts.
Interrupted running steps remain blocked awaiting reconciliation. There is no
blind retry of possible side effects. A background daemon performs at most
one approved step each poll; it cannot authorise or invent missions.

## Usage

On a reviewed checkout of main with Python, Node, systemd user manager and
Chromium installed:

    cd integrations/background-browser
    npm ci --ignore-scripts --no-audit --no-fund
    cd ../..
    python3 scripts/validate.py
    python3 autonomy_cli.py host
    python3 autonomy_cli.py submit examples/autonomy-browser-demo.json --confirm
    python3 autonomy_cli.py run-once background-demo
    python3 autonomy_cli.py run-once background-demo
    python3 autonomy_cli.py run-once background-demo
    python3 autonomy_cli.py status background-demo

Mission controls are also available locally and through the authenticated
management dashboard, with explicit confirmation. Pause and cancellation
cannot pretend to stop a running process:

    python3 autonomy_cli.py pause background-demo --confirm
    python3 autonomy_cli.py resume background-demo --confirm
    python3 autonomy_cli.py cancel background-demo --confirm

Optional continuous processing of *approved* plans only:

    bash scripts/install-autonomy-service.sh --confirm
    systemctl --user status open-ci-hub-autonomy.service
    journalctl --user -u open-ci-hub-autonomy.service --no-pager -n 20

To stop the daemon, check active transient browser units before using
systemctl --user stop open-ci-hub-autonomy.service. Uncertain work must be
reconciled before any attempt to requeue it. The daemon service has its own
MemoryMax=300M and CPUQuota=20% and polls every 60 seconds.

The authenticated management dashboard now also has confirmation-gated
pause/resume/cancel actions on idle missions (including approved mobile
editors), with owner audit records. Job IDs, status and local resource
admission are visible; page text, URLs, credentials and private job
payloads are not exposed to the dashboard. Source merge does not install
or restart the live dashboard on the current owner PC.

## Ten requested features and real status

1. Invisible headless browser: locally VERIFIED with real public HTTPS
   navigation, extraction and a private screenshot; no KDE pointer input.
2. Separate full graphical desktop: NOT YET implemented. Needs isolated
   Wayland/Xvfb compositor, GUI policy and lifecycle.
3. Foreground-first scheduler: SOURCE + resource gate and systemd limits
   TESTED; sustained interactive KDE benchmark still required.
4. API / DOM / visual AI: fast read-only HTTPS fetch, DOM interaction and
   screenshot available. Autonomous vision model NOT connected.
5. Distributed fleet: static capability/cost matching SOURCE only. No remote
   authenticated job transport or live cross-machine reservation yet.
6. Natural-language mission controller: durable fixed-operation DAG
   implemented; open-ended safe task planner NOT yet connected.
7. Permission firewall: owner approvals, host and action allowlists, no
   inherited browser tokens, and real bubblewrap filesystem/process namespace
   confinement tested. Independent network/VM isolation is still pending.
8. Work receipts: SQLite state, result hashes and fail-closed restart rules
   implemented; signed third-party attestation still pending.
9. Cost optimiser: unknown or paid workers are rejected by the zero-cost
   selector; no cloud pricing APIs or capacity reservation yet.
10. Mission control: owner/editor management dashboard now supports
    confirmation-gated pause/resume/cancel while ensuring running work remains
    safe. Source-tested; production dashboard reload and actual mobile
    pairing/revocation tests are still pending.

Local proof: 24 October 2026, a three-step DAG ran on the owner's Garuda PC.
It created successful bounded SHA/JSON results, navigated example.com using
headless Chromium, extracted body content and saved a 44KB-class JPEG evidence
artifact. The first test with a now-obsolete h1 selector failed correctly,
without reporting success. The corrected test fully completed. This does not
establish readiness for arbitrary websites, hostile content or production use.

Existing unresolved security/release PRs remain untouched and must not be
merged or deleted just to simplify the branch list. Broad unattended remote
access remains disabled pending architecture-specific security verification.

## Additional verified engineering (24 October 2026)

A real browser job launched from the Python mission worker successfully ran
Chromium inside bubblewrap and a transient systemd service, navigated to a
public HTTPS page, extracted content and produced a 44,514-byte JPEG artifact.
The plan was passed through a one-time mode-0600 file bound read-only inside
the namespace and removed after execution. Browser resource limits were
tuned against real hardware rather than disabling memory or CPU containment.

The mission API rejects unauthenticated calls, invalid actions and forged
origins; new unit/HTTP tests cover paused/running/cancelled transitions and
verified namespace mount confinement. No unrelated GitHub/GitLab pipeline,
host agent, remote PC, production app, or Cloudflare configuration was changed.

This still does NOT complete the separate virtual GUI, general visual AI,
cloud billing integration or authenticated remote fleet transport.


## Explicit reusable login state (2026-10-26)

See [browser session vault](BROWSER-SESSION-VAULT.md) for the optional
AES-GCM encrypted, keyring-protected, exact-site browser session cache.
It never reads personal browser profiles, never exports credentials to
remote workers and cannot be used for unapproved web interactions.
