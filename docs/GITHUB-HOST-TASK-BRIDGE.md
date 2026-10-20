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

## Cryptographic provenance protocol (implementation in this branch)

`request_attestation.py` verifies a **gateway-signed MCP tool invocation**.
It does not claim to sign or independently retrieve the original ChatGPT
conversation. The gateway's attestation is an independently verifiable proof
that its authentication and authorisation policy approved the exact tool task.

### ChatGPT-side gateway (not yet deployed)

Create a dedicated OAuth-protected MCP tool (e.g. `submit_host_task`). Use a
supported identity provider instead of writing a custom login service.

The gateway must:

1. Authenticate the **ChatGPT MCP client**. For a directly exposed HTTPS
   MCP service, verify OpenAI-managed mTLS client certificate chain, clientAuth
   EKU and SAN `mtls.prod.connectors.openai.com`. For Secure MCP Tunnel,
   authenticate the trusted OpenAI tunnel control path and authorisation
   separately; **do not assume the ChatGPT client certificate is forwarded to
   the local MCP service**. Do not trust spoofable forwarded identity headers.
2. Validate the **user OAuth token** signature, issuer, intended audience,
   expiry, required scopes and stable subject; bind access to the authorised
   owner's account. PKCE + resource-indicated OAuth is required for linking.
3. Accept narrowly typed tool arguments, decide whether the requested action
   fits the owner's delegated standing permissions, then sign the **canonical
   JSON of the entire task and claims** using an issuer-held Ed25519 key.
4. Forward the signed task through the designated **private GitHub control
   repository** (ID `1427302798`), never put the private signing key in
   GitHub, ChatGPT chat history or the host's working repository.
5. Record a private issuer audit receipt linking authenticated user,
   policy version, request/nonce, exact task hash and timestamp. Any claim
   about the user's *original ChatGPT words* requires additional authenticated
   transcript data from the host; the current standard MCP call does not
   supply a ChatGPT-signed copy of those words.

OpenAI's **Secure MCP Tunnel** is an option for reaching this private gateway
with outbound-only HTTPS. A standalone remote MCP server with correctly
configured HTTPS + mTLS + OAuth is an alternative. Do not assume that the
existing Tailscale-only dashboard is directly reachable from ChatGPT.

### Host verification

Host **standing-authorised** requests must contain an `attestation` map:

```json
{
  "schema": "open-ci-hub.request-attestation.v1",
  "alg": "Ed25519",
  "issuer": "https://YOUR-VERIFIED-MCP-ISSUER",
  "subject": "STABLE-VERIFIED-OAUTH-SUBJECT",
  "audience": "open-ci-hub:1427302798",
  "client": "chatgpt-mcp",
  "issued_at": 0,
  "expires_at": 0,
  "jti": "UNIQUE-URLSAFE-RANDOM-REQUEST-ID",
  "signature": "BASE64URL-ED25519-SIGNATURE"
}
```

These are **schema placeholders**, not production credentials or a valid
signed task. The issuer signs a canonical JSON object with keys `task`
(all task fields except `attestation`) and `claims` (all attestation
fields except `signature`). Canonicalisation uses UTF-8 JSON,
`sort_keys=True`, `separators=(',', ':')`, `ensure_ascii=False` and
`allow_nan=False`. The host requires a valid signature and exact configured
issuer/user match, 120-second maximum lifetime, freshness and unique JTI.
Altered/unsigned issues are ignored and never become approved work.

A public key is pinned **locally by the ordinary Garuda user**, not issued
by GitHub or self-enrolled from a request:

```fish
# Only after validating the actual OAuth issuer and obtaining its authentic
# Ed25519 PUBLIC key through a separate trusted administrative channel:
python3 github_host_bridge.py trust-issuer \
  --issuer https://YOUR-VERIFIED-MCP-ISSUER \
  --subject YOUR-STABLE-VERIFIED-OAUTH-SUBJECT \
  --public-key ~/Documents/verified-mcp-issuer-public-key.pem \
  --confirm
```

**Never place the private Ed25519 key in this directory.** Rotation is
intentional and separately audited. The local verification module requires
the standard Python `cryptography` package and fails closed if unavailable.
Don't install packages or activate this integration until its self-hosted CI
and host tests have passed.

### Evidence limits

- mTLS identifies the calling ChatGPT client; OAuth identifies the linked user.
- The issuer's Ed25519 signature proves that **that issuer** attested to the
  tool request, subject and scope, and that nobody changed them in transit.
- It is **not** an OpenAI-issued signature over the natural-language prompt
  or proof the human saw/approved every model-composed subcommand.
- GitHub authorship remains only a transport signal, never proof of consent.
- Privileged/destructive/external-commitment tasks stay outside routine
  standing approval; the signer and host both enforce that boundary.

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

**The request must also include the live gateway-generated `attestation` object.** The JSON example below illustrates only the task fields; a bare issue containing those fields will be rejected.\n\nThe next poll queues a correctly signed request automatically and attempts to write a new
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
