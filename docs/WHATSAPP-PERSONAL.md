# Personal WhatsApp writing in Open CI Hub

**Status:** Source implementation on draft PR #47. The connected Garuda PC session was unavailable during this change, so live dashboard deployment and tests on that PC still require verification.

This feature adds a **Personal WhatsApp** page to the existing authenticated dashboard, separate from CI pipelines and business email.

## What it provides

- Recipient name and telephone number fields, with UK mobile convenience formatting and support for full international numbers.
- Editable message body, with no automatic loss of your existing work when generating a writing suggestion.
- Optional personal writing suggestions for greeting, thanking, following up, checking in and apology, with warm, casual, professional and concise tones.
- Copy message and clear fields.
- **Open in WhatsApp to send**, using the officially supported click-to-chat link `https://wa.me/<international-number>?text=<encoded-message>`. The link opens a message composer in a separate browser tab, including mobile WhatsApp or WhatsApp Web. The user must review the recipient and press **Send inside WhatsApp**.
- Status messages differentiate **prepared/opened** from **actually sent**.

Official method: [WhatsApp Help Center — How to use click to chat](https://faq.whatsapp.com/5913398998672934).

## What it deliberately does NOT provide

- An unofficial personal WhatsApp API, reverse-engineered session tokens, browser scraping, session hijacking, read receipts, inbox access or automated bulk messaging.
- Automatically clicking Send for a personal account, background outbound WhatsApp messages or scheduling unsupervised personal messages.
- Stored contact lists, message archives or transmission of draft contents to Open CI Hub's server.
- A claim of delivery from a successfully opened WhatsApp link. Only WhatsApp can confirm actual message state.

**Personal WhatsApp and the WhatsApp Business Platform are different products.** Automated API sending through Meta's WhatsApp Business Platform requires a business-capable sender, applicable permissions, templates and policies; do not treat a personal WhatsApp number as automatically API-compatible.

## Privacy and security

The browser owns all draft/recipient fields. No message storage, cloud logging or account login is added to Open CI Hub. Phone numbers are validated before link generation; messages are length-limited and percent-encoded. Opening the click-to-chat link intentionally gives WhatsApp the recipient and prepared text, and the external URL may be visible in the browser history. Use the existing authenticated dashboard, review the telephone number carefully, and avoid putting highly sensitive information in the URL.

The new JavaScript is a static local dashboard asset; the existing dashboard's `script-src 'self'` CSP applies. It does not change CI workers, DNS, Docker, deployment keys, or local desktop control policies.

## Verification

After the owner PC reconnects and updates to this branch:

1. Run `node --test tests/test_whatsapp_personal.cjs`.
2. Run existing `python3 scripts/validate.py` and dashboard JavaScript syntax checks.
3. Restart `open-ci-hub-dashboard.service` during a safe idle period and check `/health`.
4. Open the authenticated dashboard's Personal WhatsApp tab and test one draft with a dummy number, without pressing Send.
5. Test the official WhatsApp handoff with a known and authorised personal contact; actual sending remains a deliberate user action.
6. Record the verification evidence in GitHub issue #48 and the existing Google Drive handover sheet.

**Rollback:** Revert the new sidebar section/script reference and the added static route; there are no message databases or migration changes.
