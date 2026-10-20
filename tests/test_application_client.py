"""App SDK validates network boundary and forms authenticated requests."""
import json
import unittest
from unittest.mock import patch
from application_client import ApplicationClient

class AppClientTests(unittest.TestCase):
    def test_accepts_only_localhost_or_real_tls_tailnet_hosts(self):
        for endpoint in ('http://127.0.0.1:8765','http://localhost:8765',
                         'https://desktop.tail8450a3.ts.net'):
            self.assertIsInstance(ApplicationClient(endpoint,'oci_v1_'+ 'x'*64),ApplicationClient)
        for endpoint in ('https://public.example.com','http://evil.example:80',
                         'https://host.ts.net.evil.com','http://host.tail.ts.net',
                         'http://127.0.0.1','http://127.0.0.1:8765/evil',
                         'https://user:pass@host.tail.ts.net',
                         'http://0.0.0.0:8765','file:///etc/passwd'):
            with self.subTest(endpoint=endpoint),self.assertRaises(ValueError):
                ApplicationClient(endpoint,'oci_v1_'+ 'x'*64)

    def test_submit_and_claim_keep_bearer_token_in_header_not_url(self):
        events=[]
        class Response:
            status=201
            def read(self,size):return b'{"job_id":"0123456789abcdef0123456789abcdef","status":"queued","duplicate":false}'
        class Conn:
            def __init__(self,*args,**kwargs):events.append(('connect',args))
            def request(self,method,path,body=None,headers=None):
                events.append(('request',(method,path,json.loads(body),headers)))
            def getresponse(self):return Response()
            def close(self):pass
        with patch('application_client.http.client.HTTPConnection',Conn):
            client=ApplicationClient('http://127.0.0.1:8765','oci_v1_'+'x'*64)
            r=client.submit('prepare',{'input':'document-1'},'ref_0123456789')
        self.assertEqual(r['status'],'queued')
        req=events[1][1]
        self.assertEqual(req[1],'/api/v1/jobs')
        self.assertEqual(req[3]['Authorization'],'Bearer '+'oci_v1_'+'x'*64)
        self.assertNotIn('oci_v1_',req[1])
