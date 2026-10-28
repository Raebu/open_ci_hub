# Open CI Hub — Gmail, Microsoft Graph, Resend and LinkedIn source access

Date: 28 October 2026. Phase: **read-only / shadow migration**.
This builds on the existing Raeburn business execution service; it does NOT
replace the five existing ChatGPT task automations, create a second CRM,
migrate any private mail or start outreach.

## Verified connected-app capabilities (not local OAuth)

The available connected ChatGPT tools were exercised directly:

| Provider | Observed result | Approved use in Open CI Hub |
| --- | --- | --- |
| Business Gmail (primary) | Connected profile and bounded recent-message ID query succeeded | Company mailbox metadata, independently authorised native OAuth required |
| Business Gmail (contact) | Connected profile and bounded recent-message ID query succeeded | Separate business inbox; no merged personal mailbox |
| Resend business account | Inbox inventory, bounded sent-mail metadata and suppression query succeeded | Only scoped support/security addresses; account-level suppression holds |
| Outlook / Microsoft Graph | Existing connector returned a PERSONAL Hotmail identity | Excluded from Raeburn business automation by policy |
| LinkedIn | Connected search returned profile URLs | Profile discovery through authorised connector only; no DM, session/cookie reuse, bulk scraping or account impersonation |

The Resend account inventory also contains an unrelated editorial mailbox.
That mailbox is intentionally outside the Raeburn business account allowlist.
The source probe observed 12 or more recent messages in each selected Gmail
account and 12 or more sent-email records on the Resend account, plus 10
suppression records. These are bounded probes, NOT exhaustive reconciliations
or provider-confirmed delivered new-client outreach counts. They are private,
not customer emails displayed in a public Git repository.

The LinkedIn query returned two possible matches, not identity or employer
verification. Search results are NOT automatic LinkedIn login rights or
authorisation to visit, scrape or message people.

## Version-controlled implementation

- business_channels.py: provider-specific **GET-only** adapters for Gmail
  message metadata, Microsoft Graph email metadata and Resend sent/received
  email metadata and multi-page account suppressions. Mailbox identity and
  shared-account addresses are verified before readback is accepted.
- business_channels_setup.py: idempotent private account enrolment with
  an owner-only local identity HMAC key in the desktop Secret Service.
  Neither credentials nor email addresses/prospect names are published.
- business_connector_bridge.py: bound, short-lived metadata handoff from
  existing ChatGPT connected-app checks. Strict numeric schema, 2-hour TTL;
  never presented as locally available OAuth permission.
- business_execution.py: incorporates provider health into the existing
  twice-hourly source scan; labels source coverage DEGRADED if native access
  is absent, rather than counting the ChatGPT probe as autonomous sync.
- tests: wrong-account refusal, shared-account exclusion, no outbound
  capability, one-time HMAC creation, secret hygiene, privacy-safe SQLite,
  dedupe, Message-ID correlation, pause on inbound review, suppression holds,
  provider API boundaries and expired connector probes.

## Privacy and thread handling

Source provider message IDs, RFC 5322 Message-ID, In-Reply-To, thread IDs and
email addresses are HMAC-SHA256 under a desktop-local 256-bit secret. The
business execution database stores ONLY these digests, aliases, timestamps,
status classes, and counts; no raw bodies, private addresses, personal
Outlook mail, attachment content or untrusted HTML.

Message-ID correlation across Gmail, Microsoft and Resend is supported.
For Resend, Message-ID is available on sent-mail GET responses and webhook
events (added July 2026), with In-Reply-To and References usable when a
future separately authorised sender is added.

Any inbound message not definitively identified as an automatic response
creates a contact follow-up HOLD pending thread review. Known bounce,
complaint, suppression or a company-level opt-out must prohibit further
outreach. A mere successful email API call is NOT verified delivery, human
interest, a booked meeting or settled cash.

**No sending API, social DM function or browser cookie extraction is exposed
by this module.** All outbound activity remains governed by the existing
Sales engine until a canonical provider-wide suppression and reply-state
readback plus exact idempotent send gate can be verified.

## Local setup: already performed on the Razer

    cd /mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub
    python3 business_channels_setup.py --confirm
    python3 business_channels.py accounts
    python3 business_channels.py sync
    python3 business_channels.py status
    python3 business_connector_bridge.py read
    python3 business_execution.py status

The private business_channel config and SQLite database are under
.local/business-execution, are git-ignored, owner-only and 0600.
The HMAC key is protected by the already-used GNOME Secret Service API
on the KDE desktop, NOT a plaintext file or shell environment variable.
Never rotate it blindly: historic recipient suppression identities would
become unmatchable.

## What still needs authorisation for independent operation

ChatGPT's connected Gmail, Outlook, Resend and LinkedIn access is not
portable to a background service. The Razer has NOT been given its own
independent Gmail OAuth tokens or Resend credentials. The private per-account
provider token selectors are empty; safe direct API calls therefore do NOT
run. No user credentials have been copied from another project.

For Gmail, separately authorise a native application with a narrow
gmail.readonly scope, using an approved Google OAuth client and verified
offline refresh support. Gmail restricted scopes may require verification
and handling of restricted user data. Google messages.list and history.list
are suitable for incremental reconciliation, but search may not be used
with gmail.metadata-only scope. The current bounded initial adapter is not
an exhaustive mailbox synchroniser.

For Microsoft Graph, connect an actual BUSINESS Microsoft 365 identity
with delegated Mail.Read or Mail.ReadBasic as appropriate. The currently
connected Hotmail account is excluded, and neither its mail nor its tokens
may be reused for a Raeburn business daemon.

For Resend, the public API provides account-wide suppressions and sent/
received metadata. Historically, Resend API keys offered FULL access or
SEND-only access; reading account-level data may therefore require a
more privileged scope than a pure read-only key. Resend now supports
OAuth 2.1 with PKCE/dynamic registration, but the non-sending endpoints
may require the full_access OAuth scope. Do NOT silently obtain or migrate
full-access keys to a background daemon. Use an explicitly approved
Resend OAuth consent or narrowly governed broker with read-only policies.
Never grant access to unrelated editorial or charity accounts.

For LinkedIn, the currently connected plugin can search people, but does
not offer general sending or inbox APIs. Use the returned professional URL
only as a provenance lead for manual/approved verification; do not infer
a specific profile is the intended person from a similar name. Do not
scrape LinkedIn or import browser session cookies to work around API limits.

The business execution service will keep reporting native provider access
INCOMPLETE until those independently authorised credentials and real
readback receipts exist. Its clock keeps running; it does not falsely claim
to have sent, replied to or suppressed anyone when no API call was made.

## Source documentation

Gmail API:
https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list
https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list
https://developers.google.com/workspace/gmail/api/auth/scopes

Resend:
https://resend.com/changelog/message-id-for-sent-emails
https://resend.com/changelog/suppression-list-support
https://resend.com/changelog/oauth-support

Microsoft Graph:
https://learn.microsoft.com/en-us/graph/api/user-list-messages
