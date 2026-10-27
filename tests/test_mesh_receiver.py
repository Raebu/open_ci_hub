"""Cross-device signed pure work: exact peer, persistent no-replay ledger."""
import tempfile
import unittest
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from offline_mesh_envelope import envelope
from mesh_receiver import receive

class MeshReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'state'
        self.root.mkdir(mode=0o700)
        self.private=Ed25519PrivateKey.generate()
        self.public=self.private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        self.record=envelope('garuda_host','s25_edge','json.inspect',
                             {'text':'{}'},signer=self.private,now=1000,ttl=300)

    def test_completed_receipt_persists_and_does_not_reexecute(self):
        calls=[]
        def runner(task,body,ram):
            calls.append((task,body,ram))
            return True,{'valid':True}
        first=receive(self.record,public_key=self.public,receiver='s25_edge',
                      folder=self.root,execute=True,runner=runner,now=1001)
        self.assertEqual(first['state'],'completed')
        self.assertEqual(calls,[('json.inspect',{'text':'{}'},128)])
        second=receive(self.record,public_key=self.public,receiver='s25_edge',
                       folder=self.root,execute=True,runner=runner,now=1002)
        self.assertTrue(second['duplicate'])
        self.assertEqual(len(calls),1)
        self.assertEqual(second['result_sha256'],first['result_sha256'])
        self.assertEqual((self.root/'mesh-receipts.sqlite').stat().st_mode & 0o777,0o600)

    def test_wrong_recipient_and_tampering_denied(self):
        with self.assertRaises(ValueError):
            receive(self.record,public_key=self.public,receiver='s22_edge',
                    folder=self.root,now=1001)
        forged=dict(self.record)
        forged['payload']={**self.record['payload'],'body':{'text':'malicious'}}
        with self.assertRaises(ValueError):
            receive(forged,public_key=self.public,receiver='s25_edge',
                    folder=self.root,now=1001)

    def test_expired_and_nonexecution_safe(self):
        with self.assertRaises(ValueError):
            receive(self.record,public_key=self.public,receiver='s25_edge',
                    folder=self.root,now=1300)
        outcome=receive(self.record,public_key=self.public,receiver='s25_edge',
                        folder=self.root,now=1002)
        self.assertEqual(outcome['state'],'verified_not_executed')
        self.assertTrue(receive(self.record,public_key=self.public,receiver='s25_edge',
                                folder=self.root,execute=True,now=1003)['duplicate'])

    def test_symlinked_storage_denied(self):
        bad=Path(self.temp.name)/'bad'
        bad.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(PermissionError):
            receive(self.record,public_key=self.public,receiver='s25_edge',
                    folder=bad,now=1001)
