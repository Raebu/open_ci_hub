"""Deployment approval, release signing, source integrity and rollback tests."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from release_manager import Releases

def archive(app, *, links=False, checks=True, program=b"print('hello')\n"):
    checks_payload={key:{'status':'passed','log_sha256':'a'*64}
                    for key in ('tests','build','database')}
    if not checks:checks_payload.pop('database')
    release={'application':app,'source_commit':'b'*40,
             'reviewed_by_signer':True,'checks':checks_payload}
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w:gz') as tar:
        for name,data in [('release.json',json.dumps(release).encode()),
                          ('app/server.py',program)]:
            info=tarfile.TarInfo(name);info.size=len(data)
            tar.addfile(info,io.BytesIO(data))
        if links:
            info=tarfile.TarInfo('app/unsafe');info.type=tarfile.SYMTYPE;info.linkname='/etc/passwd'
            tar.addfile(info)
    return buffer.getvalue()

class ReleaseSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.m=Releases(self.root)
        self.m.allow('serviceapp','serviceapp.service',37771,confirm=True)
        self.m.signers.write_text('reviewer ssh-ed25519 TEST_PUBLIC_KEY\n')
        self.signature=self.root/'release.sig'
        self.signature.write_text('stub')

    def tearDown(self):
        self.tmp.cleanup()

    def signed_stage(self,data=None):
        blob=data or archive('serviceapp')
        p=self.root/'bundle.tar.gz'
        p.write_bytes(blob)
        digest=hashlib.sha256(blob).hexdigest()
        with patch('release_manager.subprocess.run',return_value=subprocess.CompletedProcess([],0)) as verify:
            result=self.m.stage('serviceapp',p,self.signature,'reviewer',digest,confirm=True)
        self.assertIn('ssh-keygen',verify.call_args.args[0][0])
        self.assertEqual(result['independent_execution_attestation'],False)
        return digest

    def test_scope_and_confirmation_gates(self):
        with self.assertRaises(PermissionError):
            self.m.allow('otherapp','other.service',1234)
        with self.assertRaises(PermissionError):
            self.m.status('notapproved')
        with self.assertRaises(RuntimeError):
            self.m.allow('serviceapp','changed.service',37771,confirm=True)
        with self.assertRaises(ValueError):
            self.m.allow('bad;rm','bad.service',1234,confirm=True)

    def test_stage_requires_explicit_approval_and_real_digest(self):
        data=archive('serviceapp')
        b=self.root/'package.tar.gz';b.write_bytes(data)
        with self.assertRaises(PermissionError):
            self.m.stage('serviceapp',b,self.signature,'reviewer',hashlib.sha256(data).hexdigest())
        with self.assertRaises(RuntimeError):
            self.m.stage('serviceapp',b,self.signature,'reviewer','0'*64,confirm=True)

    def test_valid_signed_stage_is_immutable_and_verified(self):
        digest=self.signed_stage()
        self.assertEqual(self.m._verify(self.m._root('serviceapp'),digest)['bundle_sha256'],digest)
        with self.assertRaises(RuntimeError):
            self.signed_stage()
        target=self.m._root('serviceapp')/'releases'/digest/'app'/'server.py'
        target.write_text('tampered\n')
        with self.assertRaisesRegex(RuntimeError,'modified'):
            self.m._verify(self.m._root('serviceapp'),digest)

    def test_untrusted_links_and_missing_database_checks_fail_closed(self):
        for raw in (archive('serviceapp',links=True),archive('serviceapp',checks=False)):
            bundle=self.root/'try.tar.gz';bundle.write_bytes(raw)
            digest=hashlib.sha256(raw).hexdigest()
            with patch('release_manager.subprocess.run',return_value=subprocess.CompletedProcess([],0)), \
                 self.assertRaises(RuntimeError):
                self.m.stage('serviceapp',bundle,self.signature,'reviewer',digest,confirm=True)

    def test_deploy_requires_health_and_restores_previous(self):
        d1=self.signed_stage()
        self.assertEqual(self.m.status('serviceapp')['current'],None)
        with patch.object(self.m,'_restart'),patch.object(self.m,'_health'):
            done=self.m.deploy('serviceapp',d1,confirm=True)
        self.assertEqual(done['state'],'healthy')
        self.assertEqual(self.m.status('serviceapp')['current'],d1)
        blob=archive('serviceapp',program=b"print('updated')\n")
        d2=self.signed_stage(blob)
        with patch.object(self.m,'_restart'),patch.object(self.m,'_health',side_effect=RuntimeError('unhealthy')):
            with self.assertRaisesRegex(RuntimeError,'restored'):
                self.m.deploy('serviceapp',d2,confirm=True)
        self.assertEqual(self.m.status('serviceapp')['current'],d1)
        with patch.object(self.m,'_restart'),patch.object(self.m,'_health'):
            self.m.deploy('serviceapp',d2,confirm=True)
            rolled=self.m.rollback('serviceapp',confirm=True)
        self.assertEqual(rolled['release'],d1)
        self.assertEqual(self.m.status('serviceapp')['current'],d1)

    def test_no_previous_release_no_rollback(self):
        with self.assertRaises(RuntimeError):
            self.m.rollback('serviceapp',confirm=True)

    def test_refuses_pointer_redirection(self):
        root=self.m._root('serviceapp')
        (root/'current').symlink_to('/etc/passwd')
        with self.assertRaises(RuntimeError):
            self.m.status('serviceapp')

if __name__=='__main__':
    unittest.main()
