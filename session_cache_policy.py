"""Policy for reusable browser-login state.

Browser cookies are bearer credentials, not harmless cache entries. A future
encrypted vault must enforce an exact HTTPS origin and a short expiry; this
module stores no passwords, cookies or keys.
"""
from __future__ import annotations
from autonomy_core import valid_host

def approve_site_session(host, *, owner_confirmed, ttl_hours, allow_interaction=False):
    valid_host(host)
    if owner_confirmed is not True:
        raise PermissionError("Owner must approve each reused website session.")
    if type(ttl_hours) is not int or not 1<=ttl_hours<=24*7:
        raise ValueError("Reusable login expiry must be 1 hour to 7 days.")
    if allow_interaction:
        raise PermissionError("Session reuse never implies permission to change a website.")
    return {"host":host,"ttl_hours":ttl_hours,"reuse":"read_only",
            "key_source":"desktop_keyring_required",
            "secret_storage":"encrypted_vault_opt_in",
            "note":"No plaintext login cache, automatic sign-in or credential export enabled."}
