# Open CI Hub secure host agent (Linux)

This optional component extends Open CI Hub's existing local/Tailscale dashboard with **real host-level desktop-user operations**. It is not an OpenClaw replacement for arbitrary desktop automation and is not a privileged CI executor.

## Architecture

The **CI runners remain separate Docker containers** without a host Docker socket or host-agent socket mount. This agent runs natively as your normal, non-root Linux desktop user. It listens only on a **mode 0600 UNIX-domain socket**, never a network port, and on Linux rejects callers from other UIDs using SO_PEERCRED. The existing Open CI Hub dashboard connects from that same user account and handles private local sessions / approved Tailscale mobile identities with same-origin and CSRF checks.

The dashboard does not run arbitrary commands. Supported operations are deliberately restricted to:

- Read host operating system, hostname and disk space.
- Install or update an **explicitly allowlisted Flatpak ID** to the desktop user's own installation via Flathub, without root permissions.
- Start, stop or restart an explicitly allowlisted **systemd user** .service unit. System-level/root services and the agent's own service cannot be managed.
- Inspect the individual job's eventual success/failure through an actor-bound job ID.

Native Garuda pacman updates, kernel management, sudo/root commands, interactive desktop control and arbitrary AI-generated shell scripts **are not implemented**. This is intentional: no CI push or compromised repository should be able to administer your host.

## Installation on Garuda (one-time host access)

On the actual host Linux desktop as the user running the dashboard:

~~~fish
cd /mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub
git pull --ff-only origin main
python3 -m unittest tests.test_host_agent -v
bash scripts/install-host-agent-service.sh
systemctl --user enable --now open-ci-hub-host-agent.service
python3 host_agent.py status
~~~

Do not run the host agent as root. The script installs a **disabled** systemd user unit and never silently starts it. The explicit systemctl command activates the agent. Restart the already-running Open CI Hub dashboard to load the new routes, then visit **My PC** from your local dashboard or approved mobile device. Your dashboard and agent must run as the same Linux user.

The host agent does not install Flatpak or Flathub; those must already exist on the PC before app installation works. Check with the ordinary host tools and official Flatpak installation instructions.

## Approve narrowly scoped host actions

Everything is denied until the host owner runs an explicit, local authorisation command:

~~~fish
python3 host_agent.py allow flatpak org.gnome.Calculator --confirm
python3 host_agent.py allow service example-user-daemon.service --confirm
~~~

These commands merely allow particular targets; they do not install or start anything. Approved targets populate **My PC** options in the dashboard. Use the dashboard action's *Preview and confirm* control. A preview token is short-lived, one-time, bound to the exact target/action and actor, and never shared with CI jobs. Installations run asynchronously with an individual job ID; the mobile view polls for the final result rather than falsely reporting success on acceptance.

### Using the feature from your phone

The phone must already be connected through Tailscale Serve and approved as an **editor** in Open CI Hub's Devices section. Host administration requires a separate named grant on the PC:

~~~fish
python3 host_agent.py allow mobile approved@example.com --confirm
~~~

Replace that address with the exact identity Tailscale reports for your approved device. Being an ordinary mobile editor **alone does not** grant host administration. Every modification still requires a browser confirmation. A mobile viewer cannot act. A local authenticated desktop owner has host management access only to explicitly allowlisted targets.

These approvals are kept in the git-ignored private .local/host-agent-policy.json file with mode 0600. Local host audit events are appended to private .local/host-agent-audit.jsonl, and dashboard audit records are also made. The socket and private policy are never mounted into CI containers.

## Validation and recovery

~~~fish
systemctl --user status open-ci-hub-host-agent.service
python3 host_agent.py status
journalctl --user -u open-ci-hub-host-agent.service -n 60
python3 -m unittest discover -s tests -v
~~~

If My PC says **Host agent not running**, the local user service is not installed/started, or the dashboard is running under a different Linux user. The dashboard intentionally does not auto-install or auto-start the agent.

A successful CI test **does not prove** that the host agent has been deployed to your physical Garuda OS. Installation, Flatpak real-world execution, Tailscale cross-device approval and systemd user service lifecycle must be checked on that OS before calling it operational.

For emergency disablement:

~~~fish
systemctl --user disable --now open-ci-hub-host-agent.service
~~~

There is no remote root/pacman escalation path or backdoor. Adding one would require a separately reviewed, narrow privileged helper with interactive consent; it must never be exposed directly to untrusted CI.
