"""Safety regression tests for foreground admission and offline envelopes."""
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from human_priority_policy import throttle
from offline_mesh_envelope import envelope,verify

class HumanFirstTests(unittest.TestCase):
    def test_human_and_missing_sensor_pause(self):
        self.assertEqual(throttle(seconds_since_input=None,available_mb=8000,
                         temperature_c=40)["mode"],"paused")
        self.assertEqual(throttle(seconds_since_input=0,available_mb=8000,
                         temperature_c=40)["mode"],"paused")
        self.assertEqual(throttle(seconds_since_input=900,available_mb=8000,
                         temperature_c=40,call_active=True)["mode"],"paused")
    def test_quiet_vs_idle(self):
        self.assertEqual(throttle(seconds_since_input=30,available_mb=9000,
                         temperature_c=45)["mode"],"quiet")
        self.assertEqual(throttle(seconds_since_input=200,available_mb=9000,
                         temperature_c=45)["mode"],"background")
        self.assertEqual(throttle(seconds_since_input=200,available_mb=9000,
                         temperature_c=90)["mode"],"paused")

class OfflineEnvelopeTests(unittest.TestCase):
    def test_signed_restricted_transfer_and_replay(self):
        sk=Ed25519PrivateKey.generate()
        pub=sk.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        event=envelope("galaxybook","s25","json.inspect",{"text":"{}"},signer=sk,now=1000)
        seen=set()
        self.assertTrue(verify(event,pubkey=pub,receiver="s25",
                         seen=seen,now=1001)["accepted"])
        with self.assertRaises(ValueError):
            verify(event,pubkey=pub,receiver="s25",seen=seen,now=1002)
        with self.assertRaises(ValueError):
            verify(event,pubkey=pub,receiver="s25",seen=set(),now=1400)
        with self.assertRaises(ValueError):
            envelope("galaxybook","s25","shell",{"cmd":"whoami"},
                     signer=sk,now=1000)
    def test_tampering_fails(self):
        sk=Ed25519PrivateKey.generate()
        pub=sk.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        e=envelope("galaxybook","s25","json.inspect",{"text":"{}"},signer=sk,now=1000)
        e["payload"]["body"]["text"]="modified"
        with self.assertRaises(ValueError):
            verify(e,pubkey=pub,receiver="s25",seen=set(),now=1001)
