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

## Approval model — no duplicate prompts for routine work

**Your explicit ChatGPT instruction is authorisation for routine work.** Once
the local owner deliberately enrols the private control repository and enables
the background bridge, narrowly preapproved operations execute automatically
without copying commands or tapping a second approval button.

Current standing-authorised task:

- `create_document`: creates a **new**, UTF-8 `.md` or `.txt` file (at most
  4 KiB) under `~/Documents/OpenCIHubTasks/`; never overwrites an existing file,
  executes code, accepts arbitrary paths or inherits a CI runner's credentials.

This first policy demonstrates the approval model. It is **not yet blanket
autonomy for arbitrary shell commands**. We can expand the typed operation
catalogue with explicit safety contracts for repository inspection, permitted
test execution, document/spreadsheet generation and other routine work.

The old `argv/cwd/timeout` request type is retained for advanced commands and
**still requires local approval**. Installing arbitrary software, using sudo,
deleting data, transferring money, sending unreviewed external communications
or bypassing security checks are not automatically approved just because a
GitHub account created an issue.

A critical distinction: GitHub's issue author is evidence of the **GitHub
account**, not cryptographic proof that a particular ChatGPT message came
from you. The private owner-scoped repo and typed operation policy are a
temporary constrained coordination route, not user-message authentication.
Before granting unrestricted host-user shell access without individual review,
we need a dedicated authenticated ChatGPT/Hub connector with a verifiable
principal and consent delegation.

## Trust model

1. Bind to one owner-named **private GitHub repository** using its stable ID,
   rechecking ownership/private status before each poll.
2. Accept small typed JSON requests only from the configured owner's account.
   Ignore PRs, comments, outside contributors and unsupported operations.
3. Auto-stage only valid routine document operations created **after** local
   bridge enrolment; changed issues invalidate their recorded digest.
4. Journal each request as running **before execution**, never replay after an
   interrupted run, and retain private execution results.
5. Limit work by CPU temperature, disk, memory, native runner reservations,
   scheduler pause and a cap on work intake.
6. Keep output private: GitHub gets status and hashes, not raw terminal output.
7. Keep a separate time-limited local approval path for general `argv` jobs.
   Do not silently make arbitrary scripts or terminal access standing-approved.

## What this does not yet prove

The code has not been installed on the Garuda desktop or exercised against a
real private task repository. A passing self-hosted PR test proves the code
behaved in that runner, **not that a ChatGPT request was delivered and executed
on your normal desktop**. The mobile PTY file-write test was separate.

## First-time owner setup (Garuda only, NOT performed by this documentation)

Requires existing working `gh auth status` on the **host**, Python 3 and
systemd user scopes. GitHub must permit this account to read/create comments
in a private control repo.

Use the **existing, verified private control repository**:

- GitHub: `Raebu/open-ci-chatgpt-control`
- Repository ID: `1427302798`
- Access: private, owned by `Raebu`
- Contents: [operating agreement](https://github.com/Raebu/open-ci-chatgpt-control/blob/main/README.md)

The bridge now rejects configuration against other repository names or IDs.
The control repository currently authenticates transport/ownership only. It
**does not prove the originating ChatGPT message**, so do not enable
unrestricted native shell tasks on the basis of this repository alone.

Do NOT publish customer data, credentials or private command output to Issues.

After reviewing/merging the code and updating the host without overwriting its
untracked `Service_Business/` directory:

```fish
cd /mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub
python3 github_host_bridge.py configure --repo Raebu/open-ci-chatgpt-control --sender Raebu --confirm
python3 github_host_bridge.py status
bash scripts/install-github-host-bridge-service.sh
```

The timer is installed **disabled** and starts no jobs. To enable only after
real Garuda validation and personal approval:

```fish
systemctl --user enable --now open-ci-hub-github-bridge.timer
```

This timer may auto-stage only create-only document tasks covered by the standing
owner-enrolled policy. Arbitrary argv/shell requests remain manually approved.

## Routine document request — **no second approval**

After bridge enrolment, ChatGPT can create a private GitHub Issue with title:

`[hub-task] Create an agreed document`

The **entire** body must be JSON (no Markdown fencing):

```json
{
  "schema": "open-ci-hub.host-task.v1",
  "description": "Create a document requested in ChatGPT",
  "operation": "create_document",
  "filename": "chatgpt-approved.md",
  "content": "# ChatGPT task\\n\\nDocument created on the Garuda PC.\\n"
}
```

The next poll queues this request automatically and attempts to write a new
`~/Documents/OpenCIHubTasks/chatgpt-approved.md`, subject to pressure limits
and duplicate protection. No command-line digest approval is needed for this
limited operation. **No claim of live delivery until the host file is verified.**

For other commands, keep the advanced `argv/cwd/timeout` schema and the
existing explicit local approval flow described below.

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
- Provide an authenticated standing-consent mechanism that establishes a
  verifiable ChatGPT user principal (GitHub authorship alone is insufficient).
- Add mobile approval only for privileged/high-risk tasks; ordinary task types
  must remain zero-extra-approval.
- Introduce scoped per-task approvals, host confirmation of dangerous
  filesystem actions and an explicit secret-safe output retrieval channel.
- Threat-review the tool for local co-user attacks, token rotation, hard
  process-lifecycle validation, API rate limits and backup/recovery.
- For *live interactive typing and streaming*, continue using the existing
  authenticated mobile PTY; this issue bridge is asynchronous, not a Konsole
  mirror or a remote shell that accepts arbitrary unauthorised instructions.
