"""Small standard-library client for private Open CI Hub application job API.

Runtime application tokens and worker tokens are separate capabilities.
This module never executes arbitrary code contained in a received job.
"""
from __future__ import annotations
import http.client
import json
from urllib.parse import urlsplit
import re
import ssl

PRIVATE_TAILNET=re.compile(r'^[a-z0-9][a-z0-9-]{0,62}\.[a-z0-9][a-z0-9-]{0,62}\.ts\.net$')

class ApplicationClient:
    def __init__(self,base_url:str,token:str):
        url=urlsplit(base_url)
        remote=url.scheme=='https' and url.port in (None,443) and PRIVATE_TAILNET.fullmatch(url.hostname or '')
        local=(url.scheme=='http' and url.hostname in ('127.0.0.1','localhost')
               and url.port is not None)
        if not (remote or local) or url.username or url.password or url.path not in ('','/') or url.query or url.fragment:
            raise ValueError('Use only http://localhost:PORT or verified https://HOST.tail.ts.net')
        if not isinstance(token,str) or not token.startswith('oci_v1_'):
            raise ValueError('Invalid application token format.')
        self.host=url.hostname
        self.port=url.port or 443
        self.tls=remote
        self.token=token

    def _request(self,method,path,payload=None):
        conn=(http.client.HTTPSConnection(self.host,self.port,timeout=12,
                    context=ssl.create_default_context()) if self.tls else
                    http.client.HTTPConnection(self.host,self.port,timeout=12))
        body=(json.dumps(payload,separators=(',',':')).encode('utf-8')
              if payload is not None else None)
        headers={'Authorization':'Bearer '+self.token,'Accept':'application/json'}
        if body is not None:headers['Content-Type']='application/json'
        try:
            conn.request(method,path,body=body,headers=headers)
            response=conn.getresponse()
            data=response.read(16384)
            if len(data)>8192:raise RuntimeError('Unexpectedly large API response.')
            try:value=json.loads(data)
            except (ValueError,UnicodeDecodeError):
                raise RuntimeError('Application API returned invalid JSON.') from None
            if response.status<200 or response.status>=300:
                raise RuntimeError('Application API request failed with HTTP '+str(response.status))
            if not isinstance(value,dict):raise RuntimeError('Application API response must be an object.')
            return value
        finally:
            conn.close()

    def submit(self,task,payload,idempotency_key=None):
        request={'task':task,'payload':payload}
        if idempotency_key is not None:request['idempotency_key']=idempotency_key
        return self._request('POST','/api/v1/jobs',request)

    def status(self,job_id):
        if not isinstance(job_id,str) or not re.fullmatch(r'[a-f0-9]{32}',job_id):
            raise ValueError('Invalid job ID.')
        return self._request('GET','/api/v1/jobs/'+job_id)

    def claim(self):
        return self._request('POST','/api/v1/workers/claim',{})

    def heartbeat(self,job_id,lease_token):
        return self._request('POST','/api/v1/workers/heartbeat',
                             {'job_id':job_id,'lease_token':lease_token})

    def complete(self,job_id,lease_token,success,result):
        return self._request('POST','/api/v1/workers/complete',
                             {'job_id':job_id,'lease_token':lease_token,
                              'success':success,'result':result})
