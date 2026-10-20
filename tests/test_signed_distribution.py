"""Reproducible Git archive and genuine offline SSH signature verification."""
from pathlib import Path
import hashlib
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import build_distribution, verify_distribution

class DistributionTests(unittest.TestCase):
    def test_same_commit_produces_identical_bytes_without_embedded_timestamp(self):
        sha='a'*40
        def git_run(args,**kwargs):
            if 'rev-parse' in args:
                return subprocess.CompletedProcess(args,0,sha+'\n','')
            if 'archive' in args:
                return subprocess.CompletedProcess(args,0,b'fake-git-archive-content','')
            raise AssertionError('Unexpected command')
        with tempfile.TemporaryDirectory() as td,patch('scripts.build_distribution.subprocess.run',side_effect=git_run):
            a=Path(td)/'first.tar.gz';b=Path(td)/'second.tar.gz'
            first=build_distribution.build('HEAD',a)
            second=build_distribution.build('HEAD',b)
            self.assertEqual(a.read_bytes(),b.read_bytes())
            self.assertEqual(first['sha256'],second['sha256'])
            self.assertFalse(first['signed'])
            with self.assertRaises(RuntimeError):
                build_distribution.build('HEAD',a)

    def test_unsigned_or_wrong_id_cannot_verify(self):
        with tempfile.TemporaryDirectory() as td:
            f=[Path(td)/name for name in ('archive.tar.gz','archive.tar.gz.sig','allowed')]
            for x in f:x.write_bytes(b'dummy')
            with self.assertRaises(ValueError):
                verify_distribution.verify(f[0],f[1],'bad;id',f[2])
            with patch('scripts.verify_distribution.subprocess.run',
                       return_value=subprocess.CompletedProcess([],1)):
                with self.assertRaises(RuntimeError):
                    verify_distribution.verify(f[0],f[1],'reviewer',f[2])

    @unittest.skipUnless(shutil.which('ssh-keygen'),'Real OpenSSH signature tool not installed on CI worker')
    def test_real_ssh_sign_verify_and_detect_tampering(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)
            key=path/'key';bundle=path/'release.tar.gz'
            bundle.write_bytes(b'verified release payload')
            subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],
                           check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            approved=path/'allowed-signers'
            approved.write_text('reviewer '+Path(str(key)+'.pub').read_text().strip()+'\n')
            subprocess.run(['ssh-keygen','-Y','sign','-f',str(key),'-n',
                            'open-ci-hub-deploy',str(bundle)],
                           check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            sig=Path(str(bundle)+'.sig')
            v=verify_distribution.verify(bundle,sig,'reviewer',approved)
            self.assertTrue(v['verified_signature'])
            self.assertEqual(v['sha256'],hashlib.sha256(bundle.read_bytes()).hexdigest())
            bundle.write_bytes(b'tampered payload')
            with self.assertRaises(RuntimeError):
                verify_distribution.verify(bundle,sig,'reviewer',approved)

if __name__=='__main__':
    unittest.main()
