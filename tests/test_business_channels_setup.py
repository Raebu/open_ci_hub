"""Provider enrolment never leaks or rotates keys or enables messaging."""
import json
from pathlib import Path
import tempfile
import unittest

import business_channels_setup as setup
import business_channels as ch
from tests.test_business_channels import cfg


class Fake:
    def __init__(self):
        self.value=None
        self.calls=[]
    def run(self,argv,**kwargs):
        self.calls.append(tuple(argv))
        self.value=kwargs["input"].decode().strip()
        class Result: returncode=0
        return Result()
    def lookup(self,label,purpose,run):
        return self.value


class SetupTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp=Path(temp.name)
        self.file=self.tmp/"private"/"channels.json"

    def test_requires_authority(self):
        with self.assertRaises(ch.ChannelError):
            setup.install(path=self.file)
        self.assertFalse(self.file.exists())

    def test_creates_single_owner_private_config_and_key(self):
        fake=Fake()
        a=setup.install(confirm=True,path=self.file,run=fake.run,lookup=fake.lookup)
        b=setup.install(confirm=True,path=self.file,run=fake.run,lookup=fake.lookup)
        self.assertTrue(a["configured"])
        self.assertFalse(a["send_permission"])
        self.assertEqual(len(fake.calls),1)
        self.assertEqual(self.file.stat().st_mode&0o777,0o600)
        data=json.loads(self.file.read_text())
        self.assertEqual(data["schema"],ch.SCHEMA)
        self.assertEqual([x["provider"] for x in data["accounts"]].count("gmail"),2)
        self.assertFalse(next(x for x in data["accounts"]
                              if x["provider"]=="microsoft")["enabled"])
        self.assertFalse(next(x for x in data["accounts"]
                              if x["provider"]=="linkedin")["enabled"])
        self.assertNotIn(fake.value,self.file.read_text())

    def test_conflicting_policy_is_not_replaced(self):
        fake=Fake()
        self.file.parent.mkdir()
        self.file.write_text(json.dumps(cfg()))
        self.file.chmod(0o600)
        with self.assertRaises(ch.ChannelError):
            setup.install(confirm=True,path=self.file,
                          run=fake.run,lookup=fake.lookup)
        self.assertFalse(fake.calls)

    def test_malformed_existing_key_is_not_rotated(self):
        fake=Fake()
        fake.value="not-hex-key"
        with self.assertRaises(ch.ChannelError):
            setup.init_key(run=fake.run,lookup=fake.lookup)
        self.assertEqual(len(fake.calls),0)


if __name__=="__main__":
    unittest.main()
