"""Fail-closed cost boundaries for Hub-controlled provider operations.

No environment variable, webhook or repository manifest can grant a billing
exception. Provider-side execution needs a separate reviewed admission adapter.
This is not a sandbox for owner-authorised arbitrary terminal commands.
"""

class CostPolicyError(PermissionError):
    pass


def deny_unverified_external_work(operation):
    raise CostPolicyError(
        'Zero-cost policy blocked ' + operation +
        ': no verified provider hard £0 limit and authorised local execution. '
        'Use the Hub local queue. Existing hosting is preserved; '
        'billing exceptions require explicit owner approval and a reviewed adapter.'
    )
