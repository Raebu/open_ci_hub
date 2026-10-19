# Host terminal in Open CI Hub

The Terminal tab is a **new interactive Linux PTY** running as the logged-in Linux user. It is NOT a remote mirror of an existing KDE Konsole window. That would require separate KDE display-streaming/remote desktop consent and a secure, protected input channel. The terminal shares the same desktop user permissions, home directory and filesystem as Konsole.

## Threat model

This is highly privileged relative to a normal CI dashboard. A terminal can execute arbitrary commands as its Linux user, read private files and potentially request elevated system access if the user's sudo policy permits it. Do not grant terminal access to an untrusted device or a generic CI job. Avoid entering passwords or secrets in browser commands. Open CI Hub does not store terminal input/output, but shell history and remote device software may. It audits opening/closing sessions, not every command.

- Off by default, including for local PC sessions.
- Only a local Linux user can approve the terminal capability, via host_agent.py allow terminal enabled --confirm.
- Mobile users require editor access in Devices, an approved host identity AND the separate allow terminal-mobile grant on the real PC.
- A host-side preview token must be followed by explicit user confirmation; token expires after 60 seconds and cannot be reused or transferred.
- A single interactive PTY session can exist at a time, scoped to one actor, with a five-minute idle timeout, fifteen-minute absolute maximum, and background expiry even if the phone disconnects.
- The existing host agent listens on a private 0600 Unix socket, never TCP, validates same-user socket access, and runs non-root. CI runner Docker containers do not mount the host terminal socket.
- Terminal traffic between phone and PC uses the existing private Tailscale Serve HTTPS dashboard and existing CSRF/identity validation. Do not expose dashboard or terminal publicly.

## Local owner activation (one-time)

The host agent must first be installed under the **ordinary Linux user**, not root, as described in docs/HOST-AGENT.md. Then on the Garuda machine:

~~~bash
cd /mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub
git pull --ff-only origin main
python3 host_agent.py allow terminal enabled --confirm
systemctl --user restart open-ci-hub-host-agent.service
~~~

The agent service remains opt-in. Restart the Open CI Hub dashboard too, to load the new Terminal tab.

For one named Tailscale mobile editor (replace with the exact currently paired verified identity):

~~~bash
python3 host_agent.py allow mobile approved@example.com --confirm
python3 host_agent.py allow terminal-mobile approved@example.com --confirm
~~~

The phone must already be approved as editor via Devices. Open the dashboard in your Tailscale-protected mobile browser, visit **Terminal**, choose **Start approved terminal**, confirm, then use the command input field. **Close** terminates the Linux shell and Ctrl+C sends an interrupt. For strong display/keyboard interaction, use an audited SSH terminal or KDE remote desktop solution instead.

## Limits and verification

This session cannot activate itself if the host agent was never installed on Garuda. Your current GitHub/GitLab self-hosted Docker runners cannot safely install a new host service because they are isolated from the host. A passing GitHub Actions test verifies implementation and PTY lifecycle under the test runner, **not** live mobile connectivity to your desktop.

The feature does not install or manage root packages automatically, clone an existing Konsole session, share a desktop, or provide a permanently unauthenticated shell. Remote full-host administration will remain separately approval-gated rather than granted to arbitrary commits.
