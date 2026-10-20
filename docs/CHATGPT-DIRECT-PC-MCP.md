# Direct ChatGPT → Garuda PC connection (private MCP tunnel)

**Status:** Source and tests; NOT automatically connected, enrolled, or
executed through ChatGPT until the owner completes the local and ChatGPT
steps. This is a separate, faster path from PR #34's delayed/signed GitHub
Issues host bridge. Keep existing dashboard, native CI runners, host-agent,
and legacy custom-ci-api running. Do not create public inbound ports.

## What is actually exposed

The stdio MCP server \`direct_pc_mcp.py\` offers **only**:

- \`get_pc_status\`: live RAM/disk/load and existing known user-service health,
  including Open CI Hub on 8765 and legacy CI on 8090.
- \`inspect_ci_failure\`: provider-native GitHub/GitLab run/job/log evidence
  for the exact repository/run, with existing sanitisation. This is read-only.
- \`create_pc_document\`: opt-in, new-only \`.md\`/\`.txt\` up to 4,000 bytes
  exclusively in \`~/Documents/OpenCIHubTasks\`. No overwrite or arbitrary path.

No arbitrary shell, sudo, root, SSH forwarding, GitHub repo edits, software
install, process restart, CI retry or deployment is offered by this server.

**Identity boundary:** the Secure MCP Tunnel transport authenticates its
control-plane connection through the owner's Platform credentials and associated
ChatGPT workspace. The stdio tool itself does **not** receive an OpenAI-signed
natural-language message or automatically validate an OAuth user identity.
Do not claim cryptographic origin authentication for chat messages.
For broad native-user command execution, use a separately authenticated
and explicitly scoped OAuth/mTLS gateway (see PR #34) first.
GitHub Issues alone are insufficient to prove ChatGPT instruction provenance.

## 1. Install/update the source on Garuda

Use the repo the user already has. **Never \`git clean\`, reset, delete Docker
volumes, or overwrite locally modified files.**

\`\`\`fish
cd /mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub
git status --short
git fetch origin main
# Only with a compatible, safely clean tracked working tree:
git pull --ff-only origin main
python3 -m py_compile direct_pc_mcp.py
python3 -m unittest tests.test_direct_pc_mcp -v
python3 direct_pc_mcp.py status
\`\`\`

\`status\` produces live host status, and runs as the ordinary non-root desktop
user. It does not need the OpenAI API key or a running tunnel.

## 2. Obtain a private Secure MCP Tunnel

Follow the currently published OpenAI instructions:
<https://developers.openai.com/api/docs/guides/secure-mcp-tunnels>

1. In OpenAI Platform **tunnel settings**, create one private tunnel, associate
   it with the relevant Platform organisation and target ChatGPT workspace,
   and record its \`tunnel_id\` privately.
2. Install the current official \`openai/tunnel-client\` release using its
   documented installer/download, and verify \`tunnel-client help quickstart\`.
   Do not use random community relay scripts or publish ports 8765/8090.
3. Give the tunnel client a dedicated minimal-scope runtime API key, **only
   in the owner's protected local terminal/systemd environment**. Never put
   it in this GitHub repository, an Issue, ChatGPT, screenshots or command logs.

Example from a private owner Fish shell, after the binary, tunnel and key have
been securely prepared (substitute your own tunnel id; DO NOT paste the key
into this chat):

\`\`\`fish
cd /mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub
set -gx CONTROL_PLANE_API_KEY (read -s -P "OpenAI tunnel runtime API key: ")
tunnel-client init --sample sample_mcp_stdio_local \
  --profile open-ci-garuda \
  --tunnel-id YOUR_ACTUAL_TUNNEL_ID \
  --mcp-command "/usr/bin/python3 /mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub/direct_pc_mcp.py serve"

tunnel-client doctor --profile open-ci-garuda --explain
tunnel-client run --profile open-ci-garuda
\`\`\`

This foreground run needs to stay open for ChatGPT to call the local server.
Only after testing should a dedicated non-root user systemd unit with a
protected credential store be considered. Do not reconfigure existing
\`open-ci-hub-dashboard.service\`, \`open-ci-hub-host-agent.service\`,
\`custom-ci-api.service\` or runner containers to run the tunnel.

## 3. Connect in ChatGPT

If **Add custom MCP server** is available to the user/workspace:

- Open **Plugins → + → Add custom MCP server**.
- Name it **Open CI Hub — My Garuda PC**.
- Select **Tunnel**, choose the correct tunnel or enter its \`tunnel_id\`,
  and configure any required authentication.
- Review the trust/permission warning; create the plugin.
- Confirm discovery shows **get_pc_status**, **inspect_ci_failure** and
  **create_pc_document**. Do not claim success until ChatGPT actually calls
  a tool and the returned measurements match the owner PC.

Availability and permissions depend on the ChatGPT workspace and Platform
tunnel roles. If the custom MCP option is absent or the tunnel is not visible,
do not claim it is connected; check account/workspace access first.

Relevant OpenAI instructions:
<https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt>

## 4. First two-way verification

1. Ask ChatGPT: **"From my Open CI Hub MCP, get the live status of my PC."**
   Confirm the response reports actual live disk/RAM plus service readiness.
2. Ask ChatGPT: **"Using my Open CI Hub MCP, inspect the old failed
   Raebu/open_ci_hub Actions run 39460692725."** Check run/commit/job/log
   metadata and that it does not invent missing details.
3. Only if you specifically want to test file creation, deliberately opt in
   locally as the ordinary host user:

\`\`\`fish
python3 direct_pc_mcp.py enable-documents --confirm
\`\`\`

Then ask ChatGPT to create a new \`chatgpt-connection-test.md\` file containing
unique harmless text. Verify it on the physical PC:

\`\`\`fish
ls -l ~/Documents/OpenCIHubTasks/chatgpt-connection-test.md
cat ~/Documents/OpenCIHubTasks/chatgpt-connection-test.md
\`\`\`

The file creation tool cannot overwrite an existing file or access a different
directory. Disable its standing permission at any time:

\`\`\`fish
python3 direct_pc_mcp.py disable-documents --confirm
\`\`\`

Do not upload sensitive data or expose secret file contents to the MCP tool.

## Limits

- **Remote desktop or full terminal control is NOT enabled.** This MVP is
  read-only inspection and carefully bounded document creation.
- **The original ChatGPT instruction is not cryptographically signed** in this
  stdio transport. Signed scope and user identity require a stronger gateway
  before full native shell privileges.
- **ChatGPT's ability to discover/use a custom MCP plugin** depends on your
  ChatGPT plan, workspace permissions and tunnel organisation association.
- **No automatic messages** are delivered into this conversation on background
  failures; this is a request/response tool connection.
- System health may say a process is active while the actual CI runner is
  misconfigured; check provider runner eligibility and native job proof too.

## Troubleshooting

\`python3 direct_pc_mcp.py status\` is the local no-tunnel smoke test.
For tunnel transport issues, run:
\`\`\`fish
tunnel-client doctor --profile open-ci-garuda --explain
\`\`\`

The latest \`tunnel-client\` build includes local health/ready metrics and an
admin UI. If GitHub job inspection fails, check \`gh auth status\` as the same
user running the tunnel and the exact repository permissions, but do not send
secrets to ChatGPT. Never make a failed \`gh\` call look like a successful job.
