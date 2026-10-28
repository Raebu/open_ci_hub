# Raeburn outbound email and official LinkedIn engagement — 28 October 2026

Status: OWNER POLICY INSTALLED; AUTHENTICATED PROVIDER EXECUTION STILL GATED.

## Existing execution, no duplicate systems

Open CI Hub now has the approved business_outbound.py and business_social.py
adapters, called by the existing twice-hourly business_execution.py timer.
There is no separate new scheduler, pipeline, or Chrome automation.

- Email: user-authorised policy autonomous_qualified; primary Gmail account
  supports sourced initial, Day-3/Day-7 follow-ups and legitimate replies.
  Other corporate Gmail/Resend senders support replies and service mail only.
  Private AES-encrypted message bodies, HMAC contact identities, pre-send
  reservation, strict source provenance, UK hours and bounded sends (25/day,
  maximum 2 per run). A timed-out/uncertain provider result is never blindly
  retried. Source, canonical owner and suppression must be verified first.
- LinkedIn: likes, relevant comments and comment replies via LinkedIn's
  official HTTPS REST APIs only; member OAuth, accurate identity, feed-write
  permission, relevant business content and rate limits required.
  Follow/connection requests and private LinkedIn messages are NOT available
  via existing plugin or verified partner API; no prohibited web bots.
- Posting: The EXISTING Raeburn LinkedIn Dual AutoPoster is the only post
  publisher. Open CI Hub's social policy was migrated to remove duplicate
  publishing from its own queue. The official AutoPoster handles personal
  and company posts, drafts, media and its pre-existing scheduler.

## Existing AutoPoster discovered and repaired

Local app: Auto_Linkedin_Poster/raeburn-linkedin-dual-autoposter

Its client ID/secret and redirect are present, but no valid personal or
company OAuth token is stored (zero rows); member and company URNs are
placeholders. Auto posting was disabled; human publication approval remains
enabled. Two unpublished drafts already exist.

Secured .env and SQLite from mode 0644 to 0600 and data directory from
0755 to 0700. Found Node26 ABI incompatibility with prebuilt better-sqlite3.
Using already-installed Node24.21.0, npm run ci passed all 10 application
tests, lint, typecheck and build. Did not create another LinkedIn publisher.

The secure admin setup was denied by the desktop tool safety control. It
has NOT been bypassed and the AutoPoster has NOT been started insecurely.
The owner must complete that secure local login and OAuth grant, including
actual feed-write permission, before account-side engagement can occur.

Open CI Hub's social adapter checks the same existing OAuth token database
read-only (only when files are private and the token has correct scope,
expiry and identity) rather than creating duplicate OAuth accounts.

## Current operational proof and remaining blockers

The outbound policy was activated on the Razer with a private AES key in
Secret Service; max 25 individually qualified client messages/day and
2 per run. Neither the independent Gmail read/send OAuth nor independently
scoped Resend credentials have been installed on the Razer. No queued
real messages and no live send or delivery receipt yet.

Existing connected ChatGPT Gmail and Resend permissions already allow all
actions. The existing Direct Client Acquisition scheduled task remains
authorised and active for evidence-backed real outbound. Local policy and
plugin permission do not transfer ChatGPT OAuth to a standalone daemon.

The LinkedIn plugin supports profile search only. Connected plugin permission
changes do not create missing like/follow/message endpoint capabilities.
Authenticated social posting/reaction access depends on LinkedIn developer
scope and its account owner's consent. For 2026 comments/reactions, the
official community API documents w_member_social_feed. No currently
connected member token meets this gate, so no live social action is claimed.

UK business email must comply with PECR and UK GDPR. Corporate subscribers
differ from sole traders and other individual subscribers; clear identity,
valid opt-out, relevance and correct handling of objections remain mandatory.

## Single-source-of-truth and cutover

The five existing ChatGPT engines are preserved. A contact being observed
is not proof that Open CI Hub owns its case. All parallel mailboxes and
suppression records must be reconciled before the owner handoff can enable
local sends; otherwise duplicate outreach remains a serious risk.
In-flight sends reserve exact idempotency keys before network I/O.
No response or buyer interest is invented from an HTTP accept receipt.

Remaining acceptance:
1. Secure OAuth for business Gmail read/send and full thread reconciliation.
2. Correct Resend scoped read/send privileges, provider suppression receipt.
3. Secure account-owner OAuth through existing LinkedIn AutoPoster and
   LinkedIn-approved comment/reaction permission; no unauthorised bot.
4. Live one-recipient Gmail/Resend send and delivery proof, suppression
   proof, and confirmed human reply handling.
5. Live permitted LinkedIn like/comment provider receipt; then 24-hour
   unattended service and no-duplicate check before task retirement.

Razer checks:
    cd /mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub
    python3 business_outbound.py status
    python3 business_social.py status
    python3 business_execution.py status

Official references:
https://learn.microsoft.com/en-us/linkedin/shared/authentication/getting-access
https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/reactions-api
https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/comments-api
https://www.linkedin.com/legal/user-agreement
https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/business-to-business-marketing/
