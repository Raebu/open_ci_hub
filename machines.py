"""Private explicit machine registry; TLS-verified Tailscale Serve reachability.

Cross-machine execution is deliberately NOT permitted by these endpoints.
Each machine owns its own local host-agent approvals and sessions.
"""
from __future__ import annotations
import http.client
import json
from pathlib import Path
import re
import ssl
from hub import secure_write

ROOT=Path(__file__).resolve().parent
REGISTRY=ROOT/'.local'/'machines.json'
NAME=re.compile(r'^[A-Za-z][A-Za-z0-9 _.-]{1,60}$')
HOST=re.compile(r'^[a-z0-9][a-z0-9-]{0,62}\.[a-z0-9][a-z0-9-]{0,62}\.ts\.net$')
MAX_MACHINES=24

def hostname(value):
    if not isinstance(value,str) or not HOST.fullmatch(value):
        raise ValueError('Machine must use its exact validated Tailscale MagicDNS .ts.net hostname.')
    return value

def read():
    if REGISTRY.is_symlink() or REGISTRY.parent.is_symlink():
        raise RuntimeError('Machine registry symlink refused.')
    if not REGISTRY.is_file(): return []
    try: data=json.loads(REGISTRY.read_text(encoding='utf-8'))
    except (OSError,ValueError):
        raise RuntimeError('Machine registry corrupt; refusing inventory changes.') from None
    if not isinstance(data,list) or len(data)>MAX_MACHINES or any(
           not isinstance(x,dict) or not NAME.fullmatch(str(x.get('name','')))
           or not HOST.fullmatch(str(x.get('host',''))) for x in data):
        raise RuntimeError('Machine registry contains invalid entries.')
    keys=[x['host'] for x in data]
    if len(keys)!=len(set(keys)):raise RuntimeError('Duplicate Tailscale machine identity.')
    return data

def add(name,host,*,confirm=False):
    if not isinstance(name,str) or not NAME.fullmatch(name) or name!=name.strip():
        raise ValueError('Invalid friendly machine name.')
    host=hostname(host)
    if confirm is not True:raise PermissionError('Confirm exact machine identity before saving.')
    data=read()
    if any(x['host']==host or x['name'].casefold()==name.casefold() for x in data):
        raise RuntimeError('Machine already registered; do not silently replace it.')
    if len(data)>=MAX_MACHINES:raise RuntimeError('Machine limit reached.')
    data.append({'name':name,'host':host})
    secure_write(REGISTRY,json.dumps(data,indent=2)+'\n')
    return {'name':name,'host':host,'url':'https://'+host+'/'}

def remove(host,*,confirm=False):
    host=hostname(host)
    if confirm is not True:raise PermissionError('Explicit machine removal confirmation required.')
    data=read();remaining=[x for x in data if x['host']!=host]
    if len(remaining)==len(data):raise ValueError('Unknown machine.')
    secure_write(REGISTRY,json.dumps(remaining,indent=2)+'\n')
    return {'removed':host}

def probe(host):
    host=hostname(host)
    # HTTPSConnection does not follow redirects. DNS/cert trust is validated
    # by standard TLS hostname verification, with a hard per-request timeout.
    try:
        with http.client.HTTPSConnection(host,443,context=ssl.create_default_context(),timeout=3) as conn:
            conn.request('GET','/health',headers={'Accept':'application/json'})
            result=conn.getresponse()
            raw=result.read(1024)
        doc=json.loads(raw)
        if result.status==200 and isinstance(doc,dict) and doc.get('ok') is True:
            return {'reachability':'dashboard-reachable',
                    'note':'Only /health was verified; runners and host agent may still be offline.'}
        return {'reachability':'unverified',
                'note':'Remote HTTPS endpoint did not provide a valid Open CI Hub health response.'}
    except (OSError,ValueError,ssl.SSLError,http.client.HTTPException,TimeoutError):
        return {'reachability':'unreachable',
                'note':'Tailscale Serve, TLS, network or remote hub unavailable. No remote changes made.'}

def inventory(*,check=False):
    items=[]
    for x in read():
        item={'name':x['name'],'host':x['host'],'url':'https://'+x['host']+'/',
              'reachability':'not-checked',
              'note':'Select Check to verify the remote dashboard over Tailscale HTTPS.'}
        if check:item.update(probe(x['host']))
        items.append(item)
    return {'machines':items,'remote_management':'each-machine-own-approved-session',
            'central_remote_command_execution':False}
