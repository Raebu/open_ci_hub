# Private browser-login session vault — 26 October 2026

This is not a passwords file. Browser cookies and localStorage often contain
bearer session tokens and must be protected like passwords. Open CI Hub stores
them as AES-256-GCM ciphertext, one exact HTTPS host per encrypted file.
The 256-bit key belongs to the owner's Linux desktop Secret Service/KWallet
compatible keyring, not the source repository, CI, an environment file or
remote worker. It fails closed when the keyring is locked.

The vault does not read a personal Chrome profile or extract logins without
manual owner action. Cloud/mobile workers never receive its keys or sessions.

## 1. Initialise on the owner Garuda desktop

    python3 browser_session_vault.py init --confirm

The desktop Secret Service must be unlocked. Do not bypass keyring security
or paste the encryption key into chat or GitHub.

## 2. Sign into a website yourself

Replace example.com with the exact website you own the login for:

    node integrations/background-browser/capture-session.mjs example.com

A new, separate visible Chromium window opens. Sign in yourself, return
to https://example.com/ after any SSO redirect, then press Enter in the
Konsole session running the capture.

The capture writes only exact-site secure cookies and HTTPS localStorage
to a private mode-0600 temporary file in RAM at /dev/shm. Passwords
are never typed into ChatGPT. Some sites requiring parent-domain cookies,
sibling-domain SSO or IndexedDB cannot use this strict mode.

The capture prints its temporary path, not cookie contents. Import it:

    python3 browser_session_vault.py import example.com --state /dev/shm/oci-session-REPLACE.json --ttl-hours 24 --confirm

Replace the path with the one actually printed, then immediately unlink
the plaintext RAM-only copy after successful import:

    rm -- /dev/shm/oci-session-REPLACE.json

Never put plaintext sessions in the project folder. The encrypted vault
rejects nonprivate input files, unexpected storage, symlinks, or overwrites.

## Optional: standing permission, no repeated login prompt per mission

To let approved read-only browser missions automatically use the saved
GitHub session, grant access **once** for this exact host:

    python3 browser_session_vault.py allow-auto github.com --days 90 --confirm

No additional `--confirm-session` is needed for eligible queued missions.
The background daemon checks the standing authorisation itself and may use
the saved GitHub session automatically when a mission needs to browse
`https://github.com`. The approval is HMAC-authenticated by your existing
desktop keyring key, private, per host and revocable. It does not export
passwords or cookies to agents, and never authorises browser clicks, fills,
submissions or cross-site navigation. Missions are still required to have
already been approved through the normal Open CI Hub controls.

Review approved sites:

    python3 browser_session_vault.py auto-list

Turn off this automatic access without deleting the encrypted login:

    python3 browser_session_vault.py deny-auto github.com --confirm

The standing grant can last up to 90 days, **but website sessions have their
own expiry**. The current GitHub vault record has its own independently
enforced expiry; granting standing consent does not extend it, silently
renew GitHub cookies or bypass GitHub MFA. Once the saved session expires,
background access safely defers and must be refreshed by a normal owner
sign-in. For a longer local cache on your next import, the vault supports
`--ttl-hours 168` (seven days), provided the website's own session still
works.

This grants reusable identity, **not** general permission to edit code,
merge pull requests, send messages or perform financial operations.
Those still require their own authorised tool/API scopes and workflow rules.

## 3. Reuse for one approved read-only browser mission step

A browser mission using login reuse must permit exactly one HTTPS host
(example.com) and only screenshot/extract actions. No click, fill, pay,
post, messaging or deploying is permitted with a cached login. The
background daemon does not automatically reuse credentials.

    python3 autonomy_cli.py rehearse examples/YOUR-PLAN.json
    python3 autonomy_cli.py submit examples/YOUR-PLAN.json --confirm
    python3 autonomy_cli.py run-once MISSION-ID --reuse-login example.com --confirm-session

Execute any earlier digest/inspect steps WITHOUT the reuse flag. The browser
session is decrypted before the durable mission step lease, read-only mounted
inside a single private bubblewrap Chromium job, and removed afterwards.
The encryption key never enters the sandbox.

Inspect metadata and revoke local session:

    python3 browser_session_vault.py list
    python3 browser_session_vault.py revoke example.com --confirm

Local deletion does not revoke the remote website login; separately sign out
or revoke sessions at the provider if compromised.

## Acceptance and limitations

AES-GCM tampering, wrong keys/hosts, TTL, owner-only permissions, no-clobber
writes, symlink rejection and explicit consent tests pass. A real Chromium
job successfully consumed an empty synthetic storageState via the sandbox
and captured the public example.com page. No real account was signed into.

Actual reuse requires one manual website login. The sandbox still shares
host networking, so DNS rebinding and browser exploit risks require further
hardening before using financial, payments or privileged administrator sites.
Encrypted vault installation does not mean any login is already cached.
