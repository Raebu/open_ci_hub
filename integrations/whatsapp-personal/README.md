# Personal WhatsApp terminal assistant (opt-in, unofficial)

**Status: code implemented and locally tested; no account paired; no real messages read or sent.**

This is a standalone companion for Open CI Hub that builds on the provided
`whatsapp-web.js` + `LocalAuth` + `qrcode-terminal` example. It can accept
one-to-one WhatsApp Web messages, let the owner send terminal replies, and,
**only after explicit private opt-in**, generate and submit routine replies in
the owner's wording using their locally installed Ollama model
(`qwen3.5:4b`). It does not require the ChatGPT API or Work.

## WhatsApp Web October 2026 chat-list compatibility fix

On some WhatsApp Web 2.3000.x builds, the internal message key property
`_serialized` became `$1`. Unmodified `whatsapp-web.js@1.34.7` can
report `Cannot list contacts: r` even when the app says `Connected`,
and its send-message receipt may incorrectly be undefined. This is a
documented upstream compatibility regression, **not evidence of a lost session**.

This integration includes a reviewed version-specific patch at
`patches/whatsapp-web.js+1.34.7.patch`. It restores the key fallback,
prevents a single invalid chat model from failing the entire chat list,
and protects send-message ID handling. The version-and-SHA-verified `scripts/ensure-compat.js` applies the reviewed
patch after npm installation and before startup; unrecognised library versions or
unexpected dependency file changes fail closed.
The matching regression tests exercise both ID formats.

**After updating the working tree**: in the **existing interactive WhatsApp
terminal**, type `quit`; then run `npm start` and, after the
`Connected` message, run `contacts` again. Because the existing
`LocalAuth` directory is untouched, a new QR scan should not normally be
necessary. Do NOT delete the private session or run two instances with
the same profile. The current already-running Node/Chromium process must
restart to reload the patched injected library.

**No WhatsApp account or chats were accessed during the patch/unit test.**
The fix has not yet been confirmed against your account after restart.

Relevant upstream reports:
- https://github.com/wwebjs/whatsapp-web.js/issues/201845
- https://github.com/wwebjs/whatsapp-web.js/issues/201919

## Delivery troubleshooting — 22 October 2026

A one-off manual message displayed `submitted` on the terminal, but its
intended recipient reported no message. Local owner-only audit records show
the submitted message and an exact message-ID-matched WhatsApp
`outgoing-ack-3` (normally reported as *read*), nine milliseconds earlier.
**That is NOT proof that the intended phone/contact saw the message.**
There may be an unexpected phone-number-to-LID association or a
WhatsApp Web acknowledgement/UI mismatch. The assistant must not retry the
same message automatically.

A second attempted `send 204...@lid ...` was rejected by the old phone-only
parser before sending. The updated terminal provides:

- `outbox` — last five locally submitted outgoing messages and their
  ID-matched WhatsApp acknowledgement states; no contact names or raw
  message IDs are printed.
- `resolve +447000000000` — inspect **without sending** the account ID
  resolved by WhatsApp for that number and any accessible existing chat title.
  Compare this against the intended person's WhatsApp profile.
- `contacts` — the existing one-to-one chat IDs, including those ending
  in `@lid`.
- `send <existing-id@lid> Your message` — **manual only**, accepts an
  exact chat ID only when the WhatsApp library verifies the ID is an
  existing individual, non-read-only chat. It never sends to an unknown,
  group, broadcast or channel identifier.

The WhatsApp Web values `ack=1`, `ack=2`, and `ack=3` normally mean
server accepted, device acknowledged, and read reported, respectively.
They do **not** independently verify who owns an internally resolved LID.
When delivery or recipient identity is disputed, use `resolve` and verify
the recipient in the phone's WhatsApp interface before sending again.

The **already-running** terminal must type `quit` and then
`npm start` to load these new commands. Saved LocalAuth sessions are
preserved; do not delete them or start a second client on the same session.
Automatic sending remains governed by the private policy file and is
not enabled by this change.

## Testing your own number does not verify delivery

If you send from a WhatsApp account **to the same account's own phone number**,
you may not receive an incoming notification: check WhatsApp's **Message
Yourself** conversation, where the message can appear as your own outgoing
message rather than as one received from somebody else.

The local audit may show an ACK 3 (WhatsApp Web's read acknowledgement)
for the exact message ID, but **neither that nor `sendMessage()` success
independently proves that another phone/account received it**. This was
observed after a one-off test on 22 October 2026: one submit record and one
matched read acknowledgement at 09:53 BST, with no independently verified
recipient identity. No additional message was sent during diagnosis.

For a real delivery test, use a **different**, consenting WhatsApp account,
verify the recipient with `resolve +<international number>` without sending,
and confirm they can see the message on their own device after one
owner-approved manual send. Use `outbox` to view acknowledgements only.
Don't repeatedly resend on ambiguous receipt or assume that an ACK means
delivery. `send` will reject sending to the linked account itself when
the account identifiers allow it to be recognised.

This feature is not yet live-tested after restart on the owner's phone.
The already-running Node process needs an owner-controlled `quit` and
`npm start` to load new recipient checks. Existing LocalAuth files are
retained. Automatic sending remains opt-in.

## Important risk

**whatsapp-web.js is an unofficial WhatsApp Web client.**
WhatsApp may restrict or ban accounts that send unauthorised automated messages,
even if they are personal and intended to be helpful. This integration does not
claim to be endorsed by Meta or to avoid detection. It neither modifies
WhatsApp's protections nor spoofs device identity. Using headless Chromium
is not a guarantee of reliability or permission.

Official policy: https://faq.whatsapp.com/361005896189245/
Library docs: https://wwebjs.dev/guide/creating-your-bot/authentication.html

The app is configured with **autoSend:false**, an **empty approvedChats**
list and a blank writing-style file. Nothing is sent without choosing exact
one-to-one contacts, providing your own writing examples, authenticating
your device and explicitly enabling autoSend.

## First use (owner desktop)

From this directory:

```bash
npm ci --ignore-scripts
npm test
npm run init
npm start
```

The laptop needs /usr/bin/chromium and Node 22+. Browser launch was validated
on the local Garuda laptop. The pinned Puppeteer override is used with the
system Chromium instead of downloading a Chromium binary.

When started interactively on the owner PC, a QR appears **in that terminal**.
On your own phone open WhatsApp → Settings/More → Linked devices →
Link a device and scan the QR. Do not show the QR or session contents to
untrusted people. A daemon will NEVER print the QR to system logs.

In the live terminal, type:

- `help` — available commands;
- `contacts` — on-demand display of recent one-to-one chat IDs to select
  approved recipients (does not reply or export messages);
- `chats` — approved recent messages seen since this run;
- `reply 0 Hello!` — send exactly that text to the displayed approved
  chat index; check the index first;
- `status` — current connection, mode and number of approved chats;
- `quit` — exit cleanly.

### Enable automatic replies deliberately

The owner-only settings are:

```
~/.config/open-ci-hub/personal-whatsapp.json
~/.config/open-ci-hub/personal-whatsapp-style.txt
```

Both are created with permissions 0600 in a 0700 owner directory. The JSON
contains:

```json
{
  "schema": 1,
  "autoSend": false,
  "approvedChats": [],
  "ollamaModel": "qwen3.5:4b",
  "debounceMs": 10000,
  "maxPerContactDay": 6
}
```

Only add chat IDs **you have personally reviewed and approved** from the
terminal's `contacts` command, such as `447700900123@c.us`. Don't infer
a chat's identity from a display name. Your own 10–20 example messages (not
generated by AI) belong in the style file after removing secrets and third
party personal details. The assistant won't invent your usual style if no
real examples are supplied.

Stop the process, edit private settings and restart with `npm start`.
Set `autoSend:true` **only when you're ready**. New messages from those
contacts will be batched for 5–30 seconds before generating a reply. Group
chats, unknown contacts, media, status messages and externally supplied
commands are ignored. Per-contact daily limits and content gates prevent
routine automation from replying to financial, legal, medical, emergency,
relationship-conflict, sensitive or commitment-related subjects.

The local Ollama service receives the minimal approved chat context and
style references; no external AI API is involved. The helper reads actual
system available RAM before inference and defers when under 4 GiB.

**Reply send status:** `sendMessage()` success means the WhatsApp Web
library submitted a message. Delivery/read status is different and is only
observed via message acknowledgements when available. Never describe this
as guaranteed delivery.

## Automatic replies in Martin's own writing style

When connected, the assistant can learn a private writing-style reference
automatically from **messages sent by the account owner** in the already
approved individual chats. It does not copy other people's messages into the
style file, export chat content to ChatGPT, or contact any external AI.
Only ordinary, non-sensitive outgoing text examples are selected; 5 or more
examples and at least 180 characters are required. The style file stays
owner-private at 0600.

It will **not** pretend to know information or promises absent from the
conversation. Generic or unverified AI responses can be inaccurate; replies
cover routine small talk only. Money, health, legal, distress, commitments,
relationships and private details remain held for manual review.

To see the effective mode without opening WhatsApp:

```bash
npm run mode
```

To stop automatic replies immediately without disconnecting the account:

```bash
npm run pause
```

To resume, once at least one recipient is already approved and the owner's
real writing style has been learnt:

```bash
npm run resume
```

The pause/resume settings are owner-owned and updated atomically. The running
process rereads the current setting before each auto-send, so a pause applies
without service restart. Existing one-to-one allowlists remain unchanged.
The system **never** sends unknown contacts, groups or cold outreach by default.
Model inference still requires available laptop memory; the existing 4 GiB
minimum headroom check fails closed rather than overloading the system.

On restart, the assistant loads hashed inbound message receipts from the
private audit. Previously seen or stale inbound messages are not
automatically answered again merely because WhatsApp resynchronises.

### Live readiness and model requirement (22 October 2026)

The owner account is linked and the background user service is running. The
assistant has learnt 20 private, non-sensitive examples of the owner's own
texting style from nine individually approved chats. Its mode is presently
**PAUSED**.

Automatic sending has **not** been switched on. Real local Ollama generation
did not finish within the 45-second timeout on the current laptop, including
tests of several smaller models, under substantial system load/swap use.
A text-only Qwen 2.5 1.5B model was installed for further evaluation but was
not approved as a functioning inference provider.

**Do not run `npm run resume` until the selected model can pass a bounded
draft-only quality/performance check with this private style and a normal
incoming test message.** If the machine cannot produce a timely result, use
a suitably provisioned, private remote inference host instead, while keeping
the LocalAuth session on your own authorised computer. Any such remote
transfer of private message context requires explicit owner approval and
a defined data-retention/security policy.

## Background service after pairing

An optional SYSTEMD USER unit installer is included:

```bash
sh ./install-service.sh
systemctl --user status open-ci-hub-personal-whatsapp.service
```

Installer **does not enable or start** the service. Once the phone is linked,
private settings approved, and terminal flow tested by you:

```bash
systemctl --user enable --now open-ci-hub-personal-whatsapp.service
```

Stop immediately using:

```bash
systemctl --user stop open-ci-hub-personal-whatsapp.service
```

Or set `autoSend:false`, stop and restart it. If you use the live terminal,
do not start the service simultaneously: the same `LocalAuth` profile
must never be used by two browser processes.

## Security boundaries and limitations

- Session tokens are in `~/.local/state/open-ci-hub-personal-whatsapp/session`
  with owner directory permissions, **not encrypted at rest by this app**.
  Protect your OS user, disk, backups and linked devices. Never commit these.
- Real incoming texts are visible in the interactive terminal only; daemon
  logs record only hashed chat/message identifiers and status codes. The
  audit `events.jsonl` contains hashes and timestamps, not message bodies.
- The model can still hallucinate or misinterpret context. No content filter
  or style matching is perfect. Start with manual review and one approved
  test contact; do not assume safe automatic sending merely because tests pass.
- The WhatsApp Web library can break without warning when WhatsApp changes.
  No mechanism bypasses or defeats WhatsApp restrictions.
- Sending photos, documents, calls, voice recordings and actual vocal
  voice-cloning is **not** implemented. "Martin's voice" here means your
  style of writing, not generated audio.
- No phone QR has been scanned, no auto-send has been activated, and
  this code has not been live-tested against WhatsApp messages.
- Node modules are pinned by package-lock. In this environment npm audit
  returned zero known advisories with Puppeteer 25.15.0 override; the
  override is newer than the dependency declared by whatsapp-web.js and
  therefore requires real compatibility testing after linking.
