"""Cryptographic attestation of one delegated MCP tool request.

Attests a gateway-vetted *tool request*, not the original ChatGPT chat text.
The gateway must first authenticate the ChatGPT client and end user via mTLS
and OAuth, apply its policies, then sign the exact JSON task body. GitHub cannot
authorise jobs by itself. This host module verifies a *locally pinned* key.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time

from hub import secure_write

SCHEMA = 'open-ci-hub.request-attestation.v1'
AUDIENCE = 'open-ci-hub:1427302798'
TRUSTED_ISSUER = Path(__file__).resolve().parent / '.local' / 'github-host-bridge' / 'issuer.json'
JTI = re.compile(r'^[A-Za-z0-9_-]{22,80}$')
CLIENT = 'chatgpt-mcp'
MAX_AGE = 120
MAX_FUTURE_SKEW = 30
CLAIMS = {'schema','alg','issuer','subject','audience','client',
          'issued_at','expires_at','jti','signature'}


def canonical_bytes(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def private_config(path=TRUSTED_ISSUER):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
        raise PermissionError('Owner-pinned ChatGPT signer is not configured.')
    st = path.stat()
    import os
    if st.st_uid != os.geteuid() or st.st_mode & 0o077:
        raise PermissionError('Signer trust file must be private and host-user-owned.')
    try:
        result = json.loads(path.read_text('utf-8'))
    except (OSError, ValueError):
        raise PermissionError('Invalid signer configuration.') from None
    if (not isinstance(result, dict)
        or set(result) != {'issuer', 'subject', 'public_key_pem'}
        or not isinstance(result['issuer'], str) or not result['issuer'].startswith('https://')
        or not isinstance(result['subject'], str) or not result['subject']
        or not isinstance(result['public_key_pem'], str)):
        raise PermissionError('Malformed pinned signer configuration.')
    return result


def pin_signer(issuer, subject, pem_path, *, confirm=False):
    """One-time LOCAL owner action; trust never bootstraps from GitHub issues."""
    import os
    if os.geteuid() == 0 or not confirm:
        raise PermissionError('Requires explicit non-root local owner authorisation.')
    path = Path(pem_path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2048:
        raise ValueError('Invalid signing public key file.')
    pem = path.read_bytes()
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        key = serialization.load_pem_public_key(pem)
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError('Only Ed25519 issuer public keys are supported.')
    except ImportError:
        raise RuntimeError('Python cryptography package is required; no issuer pinned.') from None
    if not isinstance(issuer, str) or not issuer.startswith('https://') or not subject:
        raise ValueError('Use an HTTPS issuer and stable verified OAuth subject.')
    if TRUSTED_ISSUER.exists() or TRUSTED_ISSUER.is_symlink():
        raise RuntimeError('Signer already pinned. Rotate only with explicit audited procedure.')
    record={'issuer':issuer, 'subject':subject, 'public_key_pem':pem.decode('ascii')}
    secure_write(TRUSTED_ISSUER, json.dumps(record,sort_keys=True)+'\n')
    return {'pinned':True, 'issuer':issuer, 'subject':subject}


def signed_payload(task):
    """Signature binds EVERY task field and every attestation claim except sig."""
    att = task['attestation']
    return canonical_bytes({
        'task': {k:v for k,v in task.items() if k != 'attestation'},
        'claims': {k:v for k,v in att.items() if k != 'signature'},
    })


def verify_attestation(task, trust=None, now=None):
    """Fail closed on missing/stale/forged signatures or other issuer identity."""
    if not isinstance(task, dict) or not isinstance(task.get('attestation'), dict):
        raise PermissionError('Missing signed ChatGPT task attestation.')
    claim = task['attestation']
    if set(claim) != CLAIMS:
        raise PermissionError('Unexpected attestation claims.')
    conf = trust if trust is not None else private_config()
    timestamp = int(time.time() if now is None else now)
    iat, exp = claim['issued_at'], claim['expires_at']
    if (claim['schema'] != SCHEMA or claim['alg'] != 'Ed25519'
        or claim['client'] != CLIENT or claim['audience'] != AUDIENCE
        or claim['issuer'] != conf['issuer'] or claim['subject'] != conf['subject']
        or type(iat) != int or type(exp) != int
        or iat > timestamp + MAX_FUTURE_SKEW or exp <= timestamp
        or iat <= timestamp - MAX_AGE or exp - iat > MAX_AGE
        or not isinstance(claim['jti'], str) or not JTI.fullmatch(claim['jti'])
        or not isinstance(claim['signature'], str)):
        raise PermissionError('Invalid, expired or unauthorised ChatGPT task attestation.')
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        key = load_pem_public_key(conf['public_key_pem'].encode('ascii'))
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError('Wrong signer key type')
        signature = base64.urlsafe_b64decode(claim['signature'] + '=' * (-len(claim['signature']) % 4))
        if len(signature) != 64:
            raise ValueError('Wrong signature length')
        key.verify(signature, signed_payload(task))
    except ImportError:
        raise PermissionError('Cannot verify signature: cryptography unavailable.') from None
    except (ValueError, TypeError, InvalidSignature):
        raise PermissionError('ChatGPT task signature invalid.') from None
    return {'issuer': claim['issuer'], 'subject': claim['subject'],
            'jti': claim['jti'], 'expires_at':exp}
