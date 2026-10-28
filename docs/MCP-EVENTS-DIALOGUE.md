# Open CI Hub ↔ ChatGPT Work: MCP Events dialogue

## Purpose

Open CI Hub can initiate signed events in a subscribed ChatGPT Work conversation,
with no paid OpenAI model API and without automating ChatGPT through a browser.
ChatGPT can respond by using hub_dialogue_reply through the existing authenticated
Open CI Hub connector. Replies are stored privately for the Hub to inspect.
The Hub may send a follow-up event, but there is no uncontrolled automatic
infinite loop.

## Protocol and current limitations

This implements the MCP Events methods server/discover, events/list,
events/subscribe and events/unsubscribe alongside existing MCP tools. It
advertises MCP 2.0 version 2026-07-28 while preserving the legacy MCP initialize
and tool calls. OpenAI currently requires MCP Events in a ChatGPT Work chat.

The existing Secure MCP Tunnel authenticates the control-plane connection but
does NOT sign individual end-user ChatGPT instructions or prove the user's
identity. Restrict the plugin to the intended ChatGPT account/workspace.

Unit tests with simulated callbacks are not proof of hosted end-to-end delivery.
MCP 2.0 negotiation, verification, signed webhook reception, agent execution and
reply to Hub must be independently demonstrated in ChatGPT Work.

## Event handling and security

Events carry an ID, channel, source and a bounded summary. They are not
unconditional instructions or independent authorisation for dangerous work.
Each callback is received from ChatGPT at subscription time and must use a
public HTTPS OpenAI/ChatGPT domain, pass a signed challenge verification, and
have a valid Standard Webhooks HMAC signature. IP addresses are checked and
pinned for TLS, local/reserved addresses and redirects are refused. Unknown
callback domains require independent verification before allowlisting.

Private state lives under .local/mcp-dialogue. Outgoing messages are capped at
20/hour with deduplication for canonical native incidents. Incoming replies
carry status and evidence. Completing a message requires non-empty evidence.

The existing autonomy daemon emits deduplicated operations events only when an
approved mission is blocked or failed. The daemon remains functional even if
ChatGPT cannot be reached, and no duplicate scheduler is created.

## Deployment and operational acceptance

1. Verify source changes and test on Open CI Hub (never GitHub-hosted CI).
2. Promote to the existing Open CI Hub checkout, preserving services and data.
3. Locally opt in using: python3 hub_dialogue_events.py enable --confirm
4. Restart only the existing open-ci-hub-tunnel.service after a health check.
5. Refresh the existing Open CI Hub plugin in ChatGPT; verify event discovery.
6. In a ChatGPT Work chat, subscribe to hub.message.created on the operations
   channel. Direct the model to inspect actual Hub evidence and send its reply
   with hub_dialogue_reply, including status and evidence.
7. Verify actual events/subscribe and signed callback challenge, not just code.
8. Send harmless verification message via the existing Hub shell:
   python3 hub_dialogue_events.py send --channel operations --text 'Please report Hub readiness without making changes.'
9. Confirm webhook HTTP 2xx and ChatGPT Work event receipt; verify matching
   reply in Hub using: python3 hub_dialogue_events.py read MESSAGE_ID
10. Test repeat events, expiry/refresh, unsubscribe and production failure
    handling before enabling routine unattended mission notifications.

Important: webhook 2xx confirms receipt, not ChatGPT execution or task completion.
ChatGPT subscription feature access and message limits still apply. There is
no guarantee of unlimited processing and no separate model billing.

To stop new events and replies immediately:
python3 hub_dialogue_events.py disable --confirm

Do not remove existing Hub CI services, runners, databases or tunnels.

## Local test commands

python3 -m py_compile direct_pc_mcp.py hub_dialogue_events.py autonomy_daemon.py
python3 -m unittest tests.test_hub_dialogue_events tests.test_direct_pc_mcp tests.test_autonomy_core tests.test_autonomy_runtime -q

These test local protocol handling and mocked callbacks, not a hosted subscription.
