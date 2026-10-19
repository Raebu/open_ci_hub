# Private multi-PC inventory and mobile switching

Open CI Hub can maintain a small, approved inventory of PCs that each host their *own* Open CI Hub dashboard through Tailscale Serve HTTPS. This is an opt-in navigation and dashboard health feature, not a cloud server, a shared remote shell or a distributed CI scheduler.

## Enrolling machines

1. Install and configure Tailscale and Tailscale Serve independently on each PC. Its Open CI Hub dashboard must stay bound to loopback; only Tailscale Serve proxies to the tailnet HTTPS hostname.
2. Open the primary hub dashboard **on the owner's own PC**, visit **My computers**, and enrol the second machine by its exact MagicDNS address such as yourhost.tailnet.ts.net. Mobile editors and unapproved devices cannot enrol machines.
3. Open Open CI Hub from your mobile device over Tailscale, navigate to **My computers**, select **Check HTTPS** to verify the remote /health endpoint using TLS certificate verification, then **Open this PC dashboard** to sign into that PC's own approved device session.

Machines are stored only in a git-ignored, owner-readable file .local/machines.json. Up to 24 names/hosts can be enrolled. The probe uses only verified .ts.net hostnames, a single direct HTTPS /health request, a three-second timeout and no redirect following. No credentials, terminal commands or tokens are sent across to remote agents.

## What an online status actually means

dashboard-reachable means an HTTPS certificate validated for that MagicDNS address and /health returned a valid Open CI Hub 200 JSON response. It does *not* mean the GitHub/GitLab runners are online, an app deployed successfully, the host agent is installed, or a physical operator approved terminal rights.

unreachable means this primary PC could not validate the other machine's web health at that moment. It does not necessarily mean the PC is powered off; Tailscale ACLs, Serve TLS or the dashboard can block the probe.

## Future remote orchestration requirements

Unified execution across PCs still needs mutual device identity, device-local approvals, secure job dispatch, capacity/lease management, signed or independently verified CI provenance, runner/repository authorisation and cross-host audit and recovery. The current multi-PC inventory deliberately **does not** proxy a mobile terminal from the primary dashboard into another host or create an unrestricted remote execution gateway.

Each PC can have its own signed local deployment and mobile-approved terminal; the mobile device navigates to that PC's authenticated private dashboard when needed.
