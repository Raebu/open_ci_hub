"""Receipt integrity and non-fabrication regression tests."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import execution_evidence as e
import execution_proof as p

class EvidenceTests(unittest.TestCase):
    def test_success_requires_actual_unittest_summary(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'.local'/'receipt.json'
            data=e.receipt(name='python-unittest',argv=[sys.executable,'-c',
                  "print('Ran 7 tests in 0.002s\\n\\nOK')"],directory=td,output=path,timeout=10)
            self.assertTrue(data['all_steps_reported_success'])
            self.assertEqual(data['steps'][0]['observed_test_count'],7)
            verified=e.verify_files(path)
            self.assertEqual(verified['integrity'],'locally-consistent')
            self.assertFalse(verified['independent_proof'])

    def test_empty_green_output_does_not_count(self):
        with tempfile.TemporaryDirectory() as td:
            data=e.receipt(name='python-unittest',argv=[sys.executable,'-c',
                  "print('Everything passed')"],directory=td,
                  output=Path(td)/'receipt.json',timeout=10)
            self.assertFalse(data['all_steps_reported_success'])
            with self.assertRaises(ValueError):
                e.verify_files(Path(td)/'receipt.json')

    def test_nonzero_exit_cannot_be_reported_pass(self):
        with tempfile.TemporaryDirectory() as td:
            data=e.receipt(name='python-unittest',argv=[sys.executable,'-c',
                  "import sys;print('Ran 2 tests in 0.1s\\n\\nOK');sys.exit(2)"],
                  directory=td,output=Path(td)/'receipt.json',timeout=10)
            self.assertEqual(data['steps'][0]['exit_code'],2)
            self.assertFalse(data['all_steps_reported_success'])

    def test_transcript_tampering_detected(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'receipt.json'
            e.receipt(name='python-unittest',argv=[sys.executable,'-c',
                      "print('Ran 2 tests in 0.01s\\n\\nOK')"],
                      directory=td,output=path,timeout=10)
            (Path(td)/'python-unittest.log').write_text('FAKE')
            with self.assertRaisesRegex(ValueError,'digest mismatch'):
                e.verify_files(path)

    def test_invalid_command_and_unsafe_output_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                e.receipt(name='python-unittest',argv=[],directory=td,
                          output=Path(td)/'receipt.json',timeout=10)
            existing=Path(td)/'target.json'
            existing.write_text('safe')
            link=Path(td)/'fake.json';link.symlink_to(existing)
            with self.assertRaises(ValueError):
                e.receipt(name='python-unittest',argv=[sys.executable,'--version'],
                          directory=td,output=link,timeout=10)
            self.assertEqual(existing.read_text(),'safe')

    def test_scrubs_tokens(self):
        clean=e.redact('Bearer secret-value\napi_key=super-secret\nnormal data')
        self.assertNotIn('secret-value',clean)
        self.assertNotIn('super-secret',clean)
        self.assertIn('normal data',clean)

    def test_receipt_never_claims_independent_proof(self):
        with tempfile.TemporaryDirectory() as td:
            data=e.receipt(name='smoke',argv=[sys.executable,'-c',"print('ok')"],
                          directory=td,output=Path(td)/'receipt.json',timeout=10)
            self.assertEqual(data['source'],'runner-reported')
            self.assertFalse(data['verified_independently'])

class ProviderInspectionTests(unittest.TestCase):
    def test_github_green_without_artifact_is_unproven(self):
        def reply(args):
            path=args[2]
            if path.endswith('/jobs?per_page=100'):
                return {'jobs':[{'name':'test','conclusion':'success','runner_name':'open-ci-hub-garuda','steps':[{'name':'Run tests','conclusion':'success'}]}]}
            if path.endswith('/artifacts?per_page=100'):
                return {'artifacts':[]}
            return {'head_sha':'a'*40,'conclusion':'success'}
        with patch('execution_proof.invoke_json',side_effect=reply):
            data=p.inspect('github','Raebu/repo','1234')
        self.assertEqual(data['verification'],'not-proven')
        self.assertFalse(data['independent_attestation'])
        self.assertEqual(data['jobs'][0]['runner'],'open-ci-hub-garuda')

    def test_gitlab_quota_exceeded_reports_missing_runner(self):
        def reply(args):
            return [{'name':'python-quality','status':'failed','runner':None,
                     'failure_reason':'ci_quota_exceeded'}] if '/jobs?' in args[2] else {'status':'failed','sha':'b'*40}
        with patch('execution_proof.invoke_json',side_effect=reply):
            data=p.inspect('gitlab','Theraeburngroup/RaeburnEmailer','2964940126')
        self.assertEqual(data['jobs'][0]['runner'],'')
        self.assertEqual(data['jobs'][0]['failure_reason'],'ci_quota_exceeded')
        self.assertEqual(data['verification'],'not-proven')

    def test_artifact_presence_is_only_runner_reported(self):
        def reply(args):
            path=args[2]
            if path.endswith('/jobs?per_page=100'):return {'jobs':[{'name':'test','conclusion':'success'}]}
            if path.endswith('/artifacts?per_page=100'):return {'artifacts':[{'name':'open-ci-hub-execution-proof','expired':False,'size_in_bytes':150}]}
            return {'head_sha':'a'*40,'conclusion':'success'}
        with patch('execution_proof.invoke_json',side_effect=reply):
            data=p.inspect('github','Raebu/repo','1234')
        self.assertEqual(data['verification'],'runner-reported')
        self.assertFalse(data['independent_attestation'])

if __name__ == '__main__':
    unittest.main()
