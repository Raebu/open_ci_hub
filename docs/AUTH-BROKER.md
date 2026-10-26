# Open CI Hub authentication — one command, one private vault

The single entrypoint is python3 auth_broker.py in the existing Open CI Hub
repository. It uses the encrypted site-session vault and desktop keyring
under ~/.local/share/open-ci-hub/sessions — not a second password database,
repository, background daemon, or directory tree.

## Available and tested

Show approved website sessions and expiry without disclosing cookies:

    python3 auth_broker.py status
    python3 auth_broker.py check github.com

Grant standing read-only access once for eligible missions:

    python3 auth_broker.py approve github.com --days 90 --confirm

Suspend automatic reuse without deleting the encrypted session:

    python3 auth_broker.py pause github.com --confirm

Remove the saved session and standing consent together:

    python3 auth_broker.py forget github.com --confirm

After an owner manually signs into a website, refresh the saved login:

    node integrations/background-browser/capture-session.mjs example.com
    python3 auth_broker.py import example.com --state /dev/shm/oci-session-REPLACE.json --ttl-days 7 --confirm
    rm -- /dev/shm/oci-session-REPLACE.json

Use the random file path shown by the capture command. The broker validates
the owner-private input and exact host, encrypts the new state and atomically
replaces the existing vault file only after a successful validation. Malformed
input leaves the old encrypted session untouched. The standing permission
remains limited to read-only single-host browser missions. Website-managed
session expiry and MFA requirements always apply.

## Not yet available

A complete privileged password/TOTP broker, automatic reauthentication
with saved account passwords, unrestricted browser interaction and a Chrome
password import are NOT implemented. A proposed privileged login runner was
blocked by the security controls of the remote connection; its incomplete
code was not retained or merged. The code here never accesses browser
password databases or exposes a plaintext secret to agents. In particular,
saved GitHub browser sessions do not authorise repository changes.

Use scoped provider OAuth/API permissions and service accounts where possible.
Any future password-based login executor will require site-specific
owner approval, exact origin isolation, independently reviewed key management,
short-lived credential delivery, rate limits, MFA handling and real-device
acceptance. Until then, a website that requires a fresh login needs owner
action through the existing isolated browser capture process.

## Repository hygiene

Everything belongs to the existing Raebu/open_ci_hub repository. Merge one
reviewed temporary branch, archive its exact head, then delete that branch.
Do not touch the existing live dashboard, CI workers or cached website sessions.
