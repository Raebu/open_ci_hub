"""App Runtime: isolated credentials, queue leases, retry and authenticated HTTP."""
from __future__ import annotations
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import app_runtime as runtime

class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.b=runtime.Broker(Path(self.tmp.name)/'state')
        from resource_guard import ResourceGate
        self.b.resource_gate=ResourceGate(memory_reader=lambda:8192,gpu_reader=lambda:{})
        self.b.register('raeburnemailer',['dispatch','prepare'],confirm=True)
        self.b.register('trg-recruitment',['cv-process'],confirm=True)
        self.submit_token=self.b.issue('raeburnemailer','submit',confirm=True)['token']
        self.worker_token=self.b.issue('raeburnemailer','worker',confirm=True)['token']
        self.submit=self.b.auth(self.submit_token)
        self.worker=self.b.auth(self.worker_token)

    def tearDown(self):self.tmp.cleanup()

    def test_registered_apps_have_private_state_and_random_keys(self):
        self.assertEqual(self.b.path.stat().st_mode & 0o077,0)
        self.assertEqual(self.b.path.parent.stat().st_mode & 0o077,0)
        self.assertEqual(self.submit['role'],'submit')
        self.assertEqual(self.worker['role'],'worker')
        with self.assertRaises(PermissionError):
            self.b.auth('oci_v1_invalid')
        with self.assertRaises(RuntimeError):
            self.b.register('raeburnemailer',['another-task'],confirm=True)

    def test_app_scope_isolated_from_other_apps_and_tasks(self):
        with self.assertRaises(PermissionError):
            self.b.submit(self.submit,'cv-process',{'id':1})
        trg=self.b.auth(self.b.issue('trg-recruitment','submit',confirm=True)['token'])
        job=self.b.submit(self.submit,'dispatch',{'recipient':'user@example.com'})
        with self.assertRaises(PermissionError):
            self.b.inspect(trg,job['job_id'])
        with self.assertRaises(PermissionError):
            self.b.submit(self.worker,'dispatch',{})
        with self.assertRaises(PermissionError):
            self.b.claim(self.submit)
        self.assertEqual(self.b.claim(self.worker)['job']['job_id'],job['job_id'])
        self.assertIsNone(self.b.claim(self.worker)['job'])

    def test_idempotency_does_not_duplicate_paid_jobs(self):
        key='payment_event_0123456'
        a=self.b.submit(self.submit,'dispatch',{'order':123},key)
        b=self.b.submit(self.submit,'dispatch',{'order':123},key)
        self.assertFalse(a['duplicate'])
        self.assertTrue(b['duplicate'])
        self.assertEqual(a['job_id'],b['job_id'])
        with self.assertRaises(RuntimeError):
            self.b.submit(self.submit,'dispatch',{'order':124},key)

    def test_worker_lease_heartbeat_and_exactly_once_completion(self):
        job=self.b.submit(self.submit,'prepare',{'doc':7})
        claimed=self.b.claim(self.worker)['job']
        with self.assertRaises(PermissionError):
            self.b.complete(self.worker,job['job_id'],'wrong-lease-token-but-with-a-long-string',True,{})
        alive=self.b.heartbeat(self.worker,job['job_id'],claimed['lease_token'])
        self.assertEqual(alive['job_id'],job['job_id'])
        completed=self.b.complete(self.worker,job['job_id'],claimed['lease_token'],
                                  True,{'ok':True})
        self.assertEqual(completed['status'],'completed')
        self.assertEqual(self.b.inspect(self.submit,job['job_id'])['result'],{'ok':True})
        with self.assertRaises(PermissionError):
            self.b.complete(self.worker,job['job_id'],claimed['lease_token'],True,{})

    def test_failed_job_requeued_bounded_retries(self):
        j=self.b.submit(self.submit,'dispatch',{'id':5})
        for i in range(3):
            claim=self.b.claim(self.worker)['job']
            self.assertEqual(claim['job_id'],j['job_id'])
            result=self.b.complete(self.worker,j['job_id'],claim['lease_token'],False,{'error':'temporary'})
            self.assertEqual(result['status'],'queued' if i<2 else 'failed')
        self.assertEqual(self.b.inspect(self.submit,j['job_id'])['attempts'],3)
        self.assertIsNone(self.b.claim(self.worker)['job'])

    def test_expired_lease_quarantined_until_owner_confirms_worker_dead(self):
        j=self.b.submit(self.submit,'prepare',{'id':7})
        claim=self.b.claim(self.worker)['job']
        with self.b._db() as db:
            db.execute('UPDATE jobs SET lease_until=? WHERE id=?',(time.time()-5,j['job_id']))
        self.assertIsNone(self.b.claim(self.worker)['job'])
        self.assertEqual(self.b.inspect(self.submit,j['job_id'])['status'],'stranded')
        with self.assertRaises(PermissionError):
            self.b.recover_stranded(j['job_id'])
        self.b.recover_stranded(j['job_id'],confirm=True)
        retry=self.b.claim(self.worker)['job']
        self.assertEqual(retry['attempt'],2)
        with self.assertRaises(PermissionError):
            self.b.complete(self.worker,j['job_id'],claim['lease_token'],True,{})
        self.assertEqual(self.b.complete(self.worker,j['job_id'],retry['lease_token'],
                           True,{'processed':True})['status'],'completed')

    def test_limits_payloads_and_disallows_arbitrary_shell_tasks(self):
        with self.assertRaises(PermissionError):
            self.b.submit(self.submit,'shell',{'command':'sudo pacman -S ssh'})
        with self.assertRaises(ValueError):
            self.b.submit(self.submit,'dispatch',{'a':'x'*3000})
        with self.assertRaises(ValueError):
            self.b.submit(self.submit,'dispatch',{'a':float('nan')})
        with self.assertRaises(ValueError):
            self.b.submit(self.submit,'dispatch','not-json-object')

    def test_revoke_and_host_authorisation_require_confirmation(self):
        with self.assertRaises(PermissionError):
            self.b.register('otherapp',['run'])
        with self.assertRaises(PermissionError):
            self.b.issue('raeburnemailer','submit')
        digest=hashlib.sha256(self.submit_token.encode()).hexdigest()
        with self.assertRaises(PermissionError):
            self.b.revoke(digest)
        self.b.revoke(digest,confirm=True)
        with self.assertRaises(PermissionError):
            self.b.auth(self.submit_token)

from tests.test_web_console import DashboardHTTPTests

class BrokerHTTPTests(DashboardHTTPTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from resource_guard import ResourceGate
        cls.console.apps.resource_gate=ResourceGate(memory_reader=lambda:8192,gpu_reader=lambda:{})
        cls.console.apps.register('integration-app',['doc-process'],confirm=True)
        cls.submit_key=cls.console.apps.issue('integration-app','submit',confirm=True)['token']
        cls.worker_key=cls.console.apps.issue('integration-app','worker',confirm=True)['token']

    def _headers(self,key):
        return {'Authorization':'Bearer '+key}

    def test_browser_session_cannot_replace_app_token(self):
        st,_,_=self.request('GET','/api/v1/apps/self')
        self.assertEqual(st,401)
        st,_,_=self.request('GET','/api/v1/apps/self',cookie=self.session())
        self.assertEqual(st,401)
        st,_,_=self.request('GET','/api/v1/apps/self',
                           headers=self._headers('oci_v1_invalid'))
        self.assertEqual(st,401)

    def test_real_api_submit_claim_and_complete(self):
        submit=self._headers(self.submit_key)
        worker=self._headers(self.worker_key)
        st,created,_=self.request('POST','/api/v1/jobs',
              {'task':'doc-process','payload':{'input':'abc'},'idempotency_key':'request-abc-0001'},
              headers=submit,origin='https://other.example')
        self.assertEqual(st,201)
        jid=created['job_id']
        st,duplicate,_=self.request('POST','/api/v1/jobs',
              {'task':'doc-process','payload':{'input':'abc'},'idempotency_key':'request-abc-0001'},
              headers=submit)
        self.assertEqual(st,200);self.assertEqual(duplicate['job_id'],jid)
        st,denied,_=self.request('POST','/api/v1/workers/claim',{},headers=submit)
        self.assertEqual(st,403)
        st,lease,_=self.request('POST','/api/v1/workers/claim',{},headers=worker)
        self.assertEqual(st,200)
        self.assertEqual(lease['job']['job_id'],jid)
        st,done,_=self.request('POST','/api/v1/workers/complete',{
            'job_id':jid,'lease_token':lease['job']['lease_token'],
            'success':True,'result':{'digest':'hello'}},headers=worker)
        self.assertEqual(st,200);self.assertEqual(done['status'],'completed')
        st,job,_=self.request('GET','/api/v1/jobs/'+jid,headers=submit)
        self.assertEqual(st,200);self.assertEqual(job['result']['digest'],'hello')

    def test_api_disallows_unapproved_task_and_role_escalation(self):
        key=self._headers(self.submit_key)
        st,_,_=self.request('POST','/api/v1/jobs',
                     {'task':'sudo','payload':{'command':'id'}},headers=key)
        self.assertEqual(st,403)
        st,_,_=self.request('POST','/api/v1/workers/complete',
                     {'job_id':'a'*32,'lease_token':'x'*48,
                      'success':True,'result':{}},headers=key)
        self.assertEqual(st,403)

    def test_app_api_cannot_be_called_without_private_transport(self):
        key=self._headers(self.submit_key)
        with patch.object(self.console,'local',return_value=False),\
             patch.object(self.console,'remote_identity',return_value=None):
            st,_,_=self.request('GET','/api/v1/apps/self',headers=key)
        self.assertEqual(st,403)

    def test_no_app_api_query_or_cookie_data(self):
        st,_,_=self.request('GET','/api/v1/apps/self?debug=1',
                           headers=self._headers(self.submit_key))
        self.assertEqual(st,400)
