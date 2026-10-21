# Direct ChatGPT → Garuda PC connection

**Status:** The expanded MCP tool surface is proposed in a review branch. It is not yet deployed to Garuda or locally enabled. Existing CI runners, dashboard, host agent, legacy CI service and tunnel unit remain separate and must not be reconfigured or duplicated.

## What the expanded tool surface provides

When the relevant local capability is enabled, the tunnel can expose these operations to ChatGPT:

- PC status and read-only GitHub/GitLab failure diagnostics.
- Create a new Markdown/text document in ~/Documents/OpenCIHubTasks.
- Browse directories and read UTF-8 text files in the approved Documents, Downloads, Desktop and Code_Projects roots.
- Edit source/text files inside Code_Projects only, using an expected SHA-256 to prevent stale overwrites and a private backup before replacement. If the original file contains credential-like values that the read tool redacts, direct editing is refused: this prevents a redaction placeholder or omitted hidden value from overwriting the original. Use a local editor for those files until a secret-preserving edit operation is available.
- Run a command as the ordinary desktop user in Code_Projects, with a maximum 300-second timeout, a 16 KiB output cap, common credential redaction and an audit record.
- Install named packages from official Arch/Garuda pacman repositories through pkexec and the normal polkit authentication flow. The tool does not run AUR PKGBUILDs or bypass the authentication prompt.
- Open HTTP/HTTPS URLs, launch a fixed set of desktop applications, capture a screenshot, list/activate/minimise/close KWin windows, type text, press keys and click the pointer where the required KDE utilities are installed and available.

File access is deliberately scoped. Secret/config/cache directories, common credential files, symlinks and binary files are blocked from the direct file tools. File reads redact common credential formats, but redaction cannot guarantee that every secret format is detected. A command run with terminal capability has the ordinary user's filesystem permissions and must be treated as powerful access.

## Owner-authorised full-access mode

The current owner has explicitly instructed that all direct-PC capabilities be enabled for the private, single-owner MCP connector. New policy state and legacy policy migrations therefore enable document creation, file browsing/reading/editing, arbitrary shell commands as the desktop user, official-repository package installation and KDE desktop control.

This is a deployment-specific trust decision, not a safe default for a shared or public connector. The shell is **not sandboxed** and can access anything the desktop user can access. It does not run as root. Anyone who can invoke this connector can exercise these permissions; the stdio MCP server does not cryptographically prove the origin of the original ChatGPT instruction. Do not reuse this configuration for a shared connector or expose the tunnel publicly.

The private local policy is stored in .local/direct-mcp/permissions.json. The audit journal is .local/direct-mcp/audit.jsonl; both are intended to remain private and must never be committed.

Available owner-only commands, run locally as the ordinary Garuda user from the repository directory:

~~~fish
python3 direct_pc_mcp.py status

# Enable only new Markdown/text documents:
python3 direct_pc_mcp.py enable-documents --confirm

# Enable browse, read and source-file editing:
python3 direct_pc_mcp.py enable-file-access --confirm

# Enable local command execution:
python3 direct_pc_mcp.py enable-terminal --confirm

# Enable official-repository package installation:
python3 direct_pc_mcp.py enable-software-install --confirm

# Enable KDE screenshot, keyboard and window tools:
python3 direct_pc_mcp.py enable-desktop-control --confirm

# Enable every capability above in one owner-confirmed operation:
python3 direct_pc_mcp.py enable-capabilities --confirm

# Disable all capabilities:
python3 direct_pc_mcp.py disable-capabilities --confirm
~~~

The owner has explicitly requested full access for this single-owner connector. To revoke that decision locally, run disable-capabilities --confirm. In particular, enable-terminal permits arbitrary commands as your Linux user; it is not a sandbox and it is not a root boundary.

## Important identity and authentication limitation

The Secure MCP Tunnel authenticates its control-plane connection. The direct stdio MCP server does **not** receive a cryptographic signature over the original natural-language ChatGPT instruction, nor does it independently receive a verified ChatGPT end-user identity. Therefore it cannot prove that each command was requested by Martin rather than another party who can invoke the same connector.

The connector was configured without OAuth. Before enabling terminal execution, software installation or desktop input, verify that the connector and tunnel are restricted to your intended ChatGPT account/workspace. If that cannot be established, keep those capabilities disabled. The signed task gateway work in PR #34 remains a separate, not-yet-live path; do not assume that a private GitHub repository or the Secure MCP Tunnel by itself proves the origin of a ChatGPT message.

## Garuda KDE desktop dependencies

Desktop actions require the tunnel process to see the live KDE session environment. The tool checks for XDG_RUNTIME_DIR and a Wayland or X11 display. The background systemd user manager may need the KDE session variables imported before a restart; the implementation reports a clear error rather than pretending an action succeeded.

Optional backends:

- spectacle or grim for screenshots.
- kdotool for KWin window discovery and actions.
- wtype for Wayland keyboard text/key input.
- ydotool plus an already configured ydotoold and suitable /dev/uinput permissions for mouse clicks. The MCP tool does not grant root permissions or alter device policy.

The screenshot tool sends the visible screen to ChatGPT. Avoid displaying passwords, payment details or other material you do not want to share. Closing a window may discard unsaved work; use that action only when explicitly intended.

## Update and verify the existing installation

First inspect the local working tree. Do not run git clean, reset local changes, remove Docker volumes or overwrite untracked files.

~~~fish
cd /mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub
git status --short
git fetch origin main
git pull --ff-only origin main
python3 -m py_compile direct_pc_mcp.py
python3 -m unittest tests.test_direct_pc_mcp -v
python3 direct_pc_mcp.py status
~~~

After the update and tests pass, restart the **existing** tunnel service so it reloads the MCP tool list:

~~~fish
systemctl --user restart open-ci-hub-tunnel.service
systemctl --user status open-ci-hub-tunnel.service --no-pager
curl -fsS http://127.0.0.1:18080/readyz
curl -fsS http://127.0.0.1:18080/health/mcp
~~~

The MCP connector caches the discovered tool list, so refresh/reconnect it in ChatGPT if the new tools do not appear after the tunnel restarts. A healthy tunnel alone does not mean a local capability is enabled or a desktop action is working.

## Rollback and disablement

Immediately disable all direct-PC capabilities locally:

~~~fish
python3 direct_pc_mcp.py disable-capabilities --confirm
~~~

Then restart the existing tunnel service if you need the disabled policy reflected in a fresh tool session. The read-only status and CI failure inspection remain available. Do not delete the tunnel, dashboard, CI services or runner containers as a rollback method.
