# ChatGPT → GitHub → Open CI Hub → Garuda (owner-approved native tasks)

**Status:** implementation proposed in a review branch; NOT installed, enabled or
verified end-to-end on the user's computer. Existing Host Terminal and mobile
PTY remain untouched. No OpenClaw, new desktop application or cloud runner.

## Why two execution paths?

- **Interactive:** Phone → authenticated Tailscale Serve → Host Terminal PTY.
  This has already been proven able to create and read a file on the real PC.
- **Asynchronous:** ChatGPT → a **private** GitHub Issues control repository →
  host-user bridge → native systemd user scope → redacted status comment.
  This lets a ChatGPT session close while a locally approved job continues.

The bridge runs on the **real Garuda PC as the ordinary Linux user**, not
inside the container runner. No Docker or GitHub Actions jobs receive host
terminal sockets. It is *not a sandbox*: an approved task has the same file
permissions and network access as the Linux user, constrained by
systemd RAM, CPU and task limits. Normal sudo authentication is never bypassed.
Do not run unknown/untrusted code or treat local approval as a malware sandbox.

## Trust model

1. Only an owner-named **private GitHub repository**, verified with its stable
   repository ID, is acceptable as the control queue. Do not use this public
   source repo or a CI log as a command queue.
2. Only Issues created by the configured owner GitHub login with the exact
   `[hub-task] ` prefix and valid small JSON body are candidates. PRs, comments,
   external contributors, and arbitrary issues cannot be commands.
3. A GitHub issue **never self-approves**. The owner on the Garuda host reviews
   the **exact argv, cwd, timeout and SHA-256 request digest** and issues a
   time-limited, one-use `approve` with `--confirm`.
4. The host re-fetches the issue and verifies its exact digest before execution.
   A changed/closed issue, mismatched repository, expired approval or unsafe
   thermal/memory/disk admission condition stops or defers execution.
5. The host writes `running` to a private journal **before spawning**. After
   interruption or lost network it never silently re-executes the same issue.
   Uncertain process termination is `stranded`, not success.
6. GitHub receives only status, exit code and an output hash. Full output remains
   in a private (0600) local receipt, because terminal logs might contain
   confidential customer data or access tokens.
7. No unattended remote root, hidden agent, authentication bypass or command
   access from a public PR. The bridge is an opt-in systemd user timer.

This is deliberately more restrictive than "ChatGPT can run anything anytime".
You can approve a powerful task, but silence, a chat message or GitHub commit
is not silently treated as host authorisation.

## First-time owner setup (Garuda only, NOT performed by this documentation)

Requires existing working `gh auth status` on the **host**, Python 3 and
systemd user scopes. GitHub must permit this account to read/create comments
in a private control repo.

Create or select a **private repo owned by Raebu**. One option if you do
not already have a suitable control repository is to explicitly create one:

```fish
gh repo create Raebu/open-ci-control --private --description "Private Open CI Hub command requests"
```

Do NOT publish customer data, credentials or private command output to Issues.

After reviewing/merging the code and updating the host without overwriting its
untracked `Service_Business/` directory:

```fish
cd /mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub
python3 github_host_bridge.py configure --repo Raebu/open-ci-control --sender Raebu --confirm
python3 github_host_bridge.py status
bash scripts/install-github-host-bridge-service.sh
```

The timer is installed **disabled** and starts no jobs. To enable only after
real Garuda validation and personal approval:

```fish
systemctl --user enable --now open-ci-hub-github-bridge.timer
```

This timer cannot approve requests. It simply checks once a minute whether
anything has already been explicitly authorised by the host owner.

## Issue format ChatGPT can create through the existing GitHub connector

Create an Issue in your selected **private control repository** with title:

`[hub-task] Create local verification document`

Its body must be **only JSON**, not a Markdown code block, e.g.

```json
{
  "schema": "open-ci-hub.host-task.v1",
  "description": "Write harmless proof of ChatGPT-requested task execution",
  "argv": [
    "/usr/bin/python3",
    "-c",
    "from pathlib import Path; p=Path.home()/'Documents'/'chatgpt-host-proof.txt'; p.parent.mkdir(parents=True,exist_ok=True); p.write_text('Created by owner-approved Open CI Hub host task\\n')"
  ],
  "cwd": "/home/theraeburngroup",
  "timeout": 30
}
```

For all tasks, the first argv element must be an **absolute executable path**.
Other argv elements are passed unchanged, without a shell interpretation by
the bridge. If you explicitly approve `/usr/bin/bash -lc ...`, shell evaluation
is possible, with the corresponding security risks.

### Review and approve a specific request on the PC

```fish
python3 github_host_bridge.py pending
# Review description, full argv, cwd and digest from the returned JSON.
python3 github_host_bridge.py approve 123 --digest EXACT_SHA256_FROM_PENDING --confirm
python3 github_host_bridge.py run-once
python3 github_host_bridge.py status
```

Issue `123` and its digest above are placeholders, not an actual job.
The active timer, once deliberately enabled, can handle the `run-once`
step automatically. Check the private issue for the result comment, and check
the actual filesystem on Garuda. A posted comment alone is not independent
proof that expected business side effects happened.

## Still required for a seamless ChatGPT-controlled desktop

- Install/activate this bridge and prove that a GitHub issue actually caused
  a file to be created in the normal host `~/Documents` directory.
- Add a separate **owner-confirmed phone approval UI** to the existing private
  Tailscale dashboard so normal approvals do not require typing into Konsole.
- Introduce scoped per-task approvals, host confirmation of dangerous
  filesystem actions and an explicit secret-safe output retrieval channel.
- Threat-review the tool for local co-user attacks, token rotation, hard
  process-lifecycle validation, API rate limits and backup/recovery.
- For *live interactive typing and streaming*, continue using the existing
  authenticated mobile PTY; this issue bridge is asynchronous, not a Konsole
  mirror or a remote shell that accepts arbitrary unauthorised instructions.
