# Persistent private dashboard service

The owner-controlled dashboard runs only on 127.0.0.1:8765.
Tailscale Serve may forward private tailnet HTTPS traffic to that local port.
No Tailscale Funnel or publicly exposed HTTP listener is configured.

The prior temporary systemd-run dashboard was lost. Install a durable
owner-user unit from a clean checkout of main:

    python3 scripts/dashboard_service.py --dry-run
    python3 scripts/dashboard_service.py --confirm

The installer refuses root, mismatched checkouts and dirty tracked source,
does not alter Tailscale or existing credentials, and does not overwrite
a differing installed unit. Matching installed units are reconciled
idempotently. It verifies systemd active state and local HTTP health.

Checks:

    systemctl --user is-enabled open-ci-hub-dashboard.service
    systemctl --user status open-ci-hub-dashboard.service
    curl -fsS http://127.0.0.1:8765/health
    tailscale serve status

This service manages the dashboard alone, not CI bridge workers or autonomous
job processes. The resource limits are 768MiB RAM, 60% CPU, nice 10, private
temporary storage and no new privileges. It binds only to localhost.
Mobile identities require existing Tailscale verification and approval. The
local owner session secret stays in the existing private state directory.

If the service is unhealthy:

    journalctl --user -u open-ci-hub-dashboard.service -n 50 --no-pager
    systemctl --user restart open-ci-hub-dashboard.service

Restart only when no critical dashboard submission is in progress. Other
CI worker units are not restarted by these instructions.

On 25 October 2026 the restored service was enabled and responded to
local health and access-denial checks. Private Tailscale HTTPS returned
HTTP 200 with a valid certificate using explicit hostname-IP mapping.
The Garuda host currently lacks local MagicDNS resolver configuration,
so normal Tailscale hostname resolution there remains a separate issue.
Do not conflate that DNS issue with an unavailable dashboard.
