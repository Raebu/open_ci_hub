# Open CI Hub — background autonomy, first operational slice

Status: implemented and tested on the Garuda host. Opt-in. NOT all ten
capabilities production-complete. It does not replace the existing runner,
host agent, CI bridges or user desktop.

## Human-first background browser

A fresh, headless Chromium instance runs with Playwright Core in an independent
transient systemd --user service. It does not use the KDE DISPLAY/WAYLAND_DISPLAY,
personal Chrome profile or personal browser cookies. It refuses downloads and
unapproved HTTPS origins. CPUQuota=60%, MemoryMax=768M, MemoryHigh=640M,
MemorySwapMax=0, IOWeight=15 and TasksMax=96 have been exercised on Garuda.

The foreground resource gate admits new work only with 4GB remaining system
RAM, low system load and acceptable known temperature. This is conservative,
but a 30-minute real user-session latency benchmark is still outstanding.

The user-level systemd service still runs as the same Unix account, so it is
not a strong isolation boundary against hostile browser exploits. Allowlisted
domains and transient profiles do not prevent every DNS rebinding or kernel
attack; untrusted browsing needs a dedicated sandbox with stronger filesystem
and network controls. Browser screenshots can contain site data and are
retained only in private job artifacts.

## Owner-approved task lifecycle

Plans are bounded JSON: mission ID; up to eight exact public DNS hosts; a cost
and time ceiling; and up to twenty steps with earlier-step dependencies.

Fixed actions: fetch (bounded HTTPS read-only GET with no redirects), browse
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

Optional continuous processing of *approved* plans only:

    bash scripts/install-autonomy-service.sh --confirm
    systemctl --user status open-ci-hub-autonomy.service
    journalctl --user -u open-ci-hub-autonomy.service --no-pager -n 20

To stop the daemon, check active transient browser units before using
systemctl --user stop open-ci-hub-autonomy.service. Uncertain work must be
reconciled before any attempt to requeue it. The daemon service has its own
MemoryMax=300M and CPUQuota=20% and polls every 60 seconds.

The existing authenticated owner dashboard gains Background Work read-only
status: job IDs, status and local resource admission. It never displays URLs,
page text, credentials or private job payloads. Source merge does not install
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
   inherited browser tokens. Strong separate-UID/VM isolation pending.
8. Work receipts: SQLite state, result hashes and fail-closed restart rules
   implemented; signed third-party attestation still pending.
9. Cost optimiser: unknown or paid workers are rejected by the zero-cost
   selector; no cloud pricing APIs or capacity reservation yet.
10. Mission control: protected, read-only owner dashboard integrated in
    source; deployment, independent mobile validation and control buttons
    not yet complete.

Local proof: 24 October 2026, a three-step DAG ran on the owner's Garuda PC.
It created successful bounded SHA/JSON results, navigated example.com using
headless Chromium, extracted body content and saved a 44KB-class JPEG evidence
artifact. The first test with a now-obsolete h1 selector failed correctly,
without reporting success. The corrected test fully completed. This does not
establish readiness for arbitrary websites, hostile content or production use.

Existing unresolved security/release PRs remain untouched and must not be
merged or deleted just to simplify the branch list. Broad unattended remote
access remains disabled pending architecture-specific security verification.
