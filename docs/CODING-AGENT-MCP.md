# Open CI Hub Coding-Agent Interface — staged implementation

**Status:** source implementation on an unmerged feature branch. Not deployed,
connected to a ChatGPT plugin, independently security reviewed, or live-proven
against the actual Garuda desktop. Do not claim unattended PC execution yet.

## What was added

The existing owner-local \`direct_pc_mcp.py\` MCP server now exposes:

| MCP tool | Scope |
| --- | --- |
| \`list_coding_workspaces\` | Names only of owner-enrolled local directories |
| \`list_coding_files\` | Directory entries under those roots, excluding hidden/sensitive paths and symlinks |
| \`read_coding_file\` | Bounded UTF-8 content and SHA-256 of an enrolled source file |
| \`write_coding_file\` | Owner-enabled atomic UTF-8 create/edit; existing files require their exact prior SHA-256 |
| \`run_coding_check\` | Owner-registered argv array, no shell, 180 s maximum; status, observed exit code, elapsed time and hashed output |

Prior MCP tools (\`get_pc_status\`, \`inspect_ci_failure\`,
\`create_pc_document\`) remain unchanged.

The code does **not** allow remote arbitrary terminal commands, sudo, hidden
credential access, deleting files, OS package installation, git push/merge, or
deployment. Tests execute repo-local code as the ordinary Garuda user and can
still have side effects. **An allowlisted check is not an OS sandbox.**
Do not allow checks for untrusted repositories without a dedicated isolated worker.

## One-time owner enrolment on Garuda (not yet run)

After security review, merging the prerequisite MCP branch, deploying the updated
code to your PC and establishing a supported authenticated MCP connection:

\`\`\`fish
cd /mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub

# Read-only workspace enrolment, using an existing exact absolute path:
python3 coding_workspace.py enrol service-business /home/theraeburngroup/Service_Business --confirm

# Optional: allow narrowly bounded source-file edits for this one workspace:
python3 coding_workspace.py enable-writes service-business --confirm

# A locally chosen, fixed, non-shell check; only the named recipe can be invoked:
python3 coding_workspace.py allow-check --confirm service-business diff-check -- /usr/bin/git diff --check

python3 coding_workspace.py status
\`\`\`

These commands are examples, not evidence of execution. Do not overwrite
the existing Service_Business repository, active branches or untracked files.
The workspace must exist at the path actually confirmed on the owner PC.

Individual file edits use optimistic concurrency. Read the file first, then
pass the returned SHA-256 as \`expected_sha256\`; use \`""\` to create a new file.
Edits fail if the file changed. No blind overwrite.

A local-only receipt is written to
\`.local/direct-mcp/coding-receipts.jsonl\`, containing file hashes, check names,
exit codes and timing, not source contents or test stdout. The connector returns
a receipt ID after the write has been read back and verified. Hash receipts
provide tamper-evident comparison to current state but **are not signed
third-party attestations**; cryptographic provenance and remote receipt verification
remain planned.

## Required connection architecture

\`\`\`text
Authorised ChatGPT MCP call
          |
          v
Custom MCP plugin + platform permission checks
          |
          v
Secure MCP Tunnel (outbound only, local tunnel-client)
          |
          v
Garuda user / direct_pc_mcp.py
          |
          v
Owner-enrolled workspace tools --> read/edit/check --> local receipts
          |
          +----------------> Open CI Hub trusted local CI execution
\`\`\`

See OpenAI's documentation for Secure MCP Tunnel and custom MCP plugin
availability. A secure tunnel needs Platform permissions, a tunnel ID, a locally
running tunnel client and an eligible ChatGPT workspace; source code alone
cannot create that account capability. The desktop must be online for live PC
operations. Do not expose localhost or host shell to public HTTP.

At the time of this implementation OpenAI's full **custom MCP write-action
support** is limited by workspace/plan eligibility; **ChatGPT Plus cannot be
assumed to have that support**. If unavailable, the source layer can still be
used by a local orchestrator or a permitted API/agent environment, but it
will not grant this chat new remote tools. Existing GitHub connector writes
operate on repository content, not directly on the live host.

## Codex-like interactive loop (next milestone)

The current tools are *primitives*; they are not yet an autonomous coding
agent. The orchestrator must:

1. Select one owner-enrolled project from the natural-language request.
2. Inspect relevant files, map changes and create an isolated worktree/branch.
3. Propose and apply minimal edits, each guarded by the old file hash.
4. Invoke registered checks and trusted local CI; collect actual exit status
   and per-step evidence, never a guessed green status.
5. Retry safe failing steps, stop on conflicts and show a diff/results summary.
6. Queue jobs durably with idempotent IDs if the chat closes, subject to
   explicit delegated permissions and machine resource limits.
7. Require interactive permission for destructive/irreversible operations,
   money movement, external commitments, root access or deployment.

Separate from the chat, an optional **local AI inference service** can continue
authorised agent reasoning when hosted chat/model access is unavailable.
It uses PC CPU/RAM/GPU and electricity instead of hosted inference minutes.
Model quality and speed are constrained by hardware, so do not equate it with
an unlimited GPT-6 subscription. On a 16 GB integrated-GPU laptop, benchmark
a small quantised coding model under the Open CI Hub resource governor before
enabling unattended operations.

## Release gates

- [ ] Code review and full test evidence on the **real** native runner
- [ ] Verify path traversal, symlink/rename races, all write/no-write gates,
      concurrent writes, secret filtering and exact command allowlists
- [ ] Security review and OS isolation for executing arbitrary repository tests
- [ ] Install on the real Garuda host without replacing active containers
- [ ] Configure an eligible authenticated direct MCP app + Secure MCP Tunnel,
      or an explicitly supported alternative; verify actual ChatGPT tool calls
- [ ] Prove on-PC read, create, edit, failed test, successful test and receipt
- [ ] Add isolated worktrees, approved git commit, rollback and agent work queue
- [ ] Wire local model, lifecycle manager, idempotent retries and resource limits
- [ ] End-to-end: natural-language ChatGPT request results in verified laptop change

Existing phone-to-PC Host Terminal evidence is real but separate from this
direct ChatGPT integration. No new runner, service or AI model has been
installed or started by this PR.

References:
- https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
- https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt
