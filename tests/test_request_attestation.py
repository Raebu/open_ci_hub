"""Real Ed25519 envelope verification, tamper resistance and expiry tests."""
import base64
import copy
import time
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import request_attestation as att

ISSUER = 'https://identity.open-ci.example'
SUBJECT = 'verified-owner-subject'


def signed_task(now=None, *, private=None, nonce='abcDEF123abcDEF123abcDEF123'):
    timestamp = int(time.time() if now is None else now)
    private = private or Ed25519PrivateKey.generate()
    pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo).decode('ascii')
    task = {
        'schema': 'open-ci-hub.host-task.v1',
        'operation': 'create_document',
        'description': 'Create explicitly requested document',
        'filename': 'test.md',
        'content': 'No shell execution',
        'attestation': {
            'schema': att.SCHEMA,
            'alg': 'Ed25519',
            'issuer': ISSUER,
            'subject': SUBJECT,
            'audience': att.AUDIENCE,
            'client': 'chatgpt-mcp',
            'issued_at': timestamp,
            'expires_at': timestamp + 90,
            'jti': nonce,
            'signature': '',
        }
    }
    sig = private.sign(att.signed_payload(task))
    task['attestation']['signature'] = base64.urlsafe_b64encode(sig).decode().rstrip('=')
    return task, {'issuer': ISSUER, 'subject': SUBJECT, 'public_key_pem': pem}


class AttestationTests(unittest.TestCase):
    def test_valid_message_is_bound_to_verified_user_and_task(self):
        task, trust = signed_task(now=10000000)
        result = att.verify_attestation(task, trust, now=10000005)
        self.assertEqual(result['subject'], SUBJECT)
        self.assertEqual(result['jti'], task['attestation']['jti'])

    def test_tampered_document_is_rejected(self):
        task, trust = signed_task(now=10000000)
        for key, value in (('content', 'different text'), ('filename', 'evil.md'),
                           ('description', 'Changed task')):
            altered = copy.deepcopy(task)
            altered[key] = value
            with self.subTest(key=key), self.assertRaises(PermissionError):
                att.verify_attestation(altered, trust, now=10000005)

    def test_wrong_identity_key_and_audience_fail_closed(self):
        task, trust = signed_task(now=10000000)
        _, other = signed_task(now=10000000)
        with self.assertRaises(PermissionError):
            att.verify_attestation(task, other, now=10000005)
        for field, value in (('subject', 'intruder'),
                             ('issuer', 'https://wrong.example'),
                             ('audience', 'other-resource'), ('client', 'github')):
            changed = copy.deepcopy(task)
            changed['attestation'][field] = value
            with self.subTest(field=field), self.assertRaises(PermissionError):
                att.verify_attestation(changed, trust, now=10000005)

    def test_expiry_future_and_unsigned_fail(self):
        task, trust = signed_task(now=10000000)
        with self.assertRaises(PermissionError):
            att.verify_attestation(task, trust, now=10000091)
        with self.assertRaises(PermissionError):
            att.verify_attestation(task, trust, now=9999000)
        unsigned = copy.deepcopy(task)
        unsigned.pop('attestation')
        with self.assertRaises(PermissionError):
            att.verify_attestation(unsigned, trust, now=10000005)

    def test_no_trust_configuration_is_accepted(self):
        task, _ = signed_task()
        with self.assertRaises(PermissionError):
            att.verify_attestation(task, trust={'issuer': ISSUER,
                                 'subject': SUBJECT,
                                 'public_key_pem': 'wrong'})
