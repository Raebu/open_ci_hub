#!/usr/bin/env python3
"""Signed, non-root deployment of immutable user service releases with rollback."""
from __future__ import annotations
import argparse
import hashlib
import http.client
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
from hub import secure_write

APP = re.compile(r'^[a-z][a-z0-9-]{1,50}$')
SERVICE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.@-]{0,100}\.service$')
DIGEST = re.compile(r'^[0-9a-f]{64}$')
COMMIT = re.compile(r'^[0-9a-f]{40}$')
SIGNER = re.compile(r'^[A-Za-z0-9._@+-]{3,150}$')
REQUIRED = ('tests','build','database')
MAX_ARCHIVE = 30 * 1024 * 1024
MAX_UNPACKED = 150 * 1024 * 1024
INVENTORY = '.open-ci-hub-inventory.json'

class Releases:
    def __init__(self,state=None):
        self.state=Path(state or Path(__file__).resolve().parent/'.local')
        if self.state.is_symlink():
            raise RuntimeError('Symlinked deployment state refused.')
        self.state.mkdir(mode=0o700,parents=True,exist_ok=True)
        os.chmod(self.state,0o700)
        self.policy=self.state/'deployment-apps.json'
        self.signers=self.state/'deployment-allowed-signers'

    def _apps(self):
        if self.policy.is_symlink():
            raise RuntimeError('Symlinked deployment policy refused.')
        if not self.policy.exists(): return {}
        try: data=json.loads(self.policy.read_text(encoding='utf-8'))
        except (OSError,ValueError):
            raise RuntimeError('Deployment policy is corrupt.') from None
        if not isinstance(data,dict):
            raise RuntimeError('Deployment policy must be an object.')
        for name,item in data.items():
            if (not isinstance(name,str) or not APP.fullmatch(name) or
                not isinstance(item,dict) or not SERVICE.fullmatch(str(item.get('service',''))) or
                type(item.get('port')) is not int or not 1<=item['port']<=65535 or
                not isinstance(item.get('path'),str) or not item['path'].startswith('/') or
                len(item['path'])>200 or any(ch in item['path'] for ch in '?#\r\n')):
                raise RuntimeError('Invalid deployment target.')
        return data

    def allow(self,app,service,port,path='/health',confirm=False):
        if not isinstance(app,str) or not APP.fullmatch(app):
            raise ValueError('Invalid app ID.')
        if not isinstance(service,str) or not SERVICE.fullmatch(service) or service=='open-ci-hub-host-agent.service':
            raise ValueError('Invalid user service.')
        if type(port) is not int or not 1<=port<=65535:
            raise ValueError('Invalid health port.')
        if not isinstance(path,str) or not path.startswith('/') or len(path)>200 or any(c in path for c in '?#\r\n'):
            raise ValueError('Invalid health path.')
        if confirm is not True:
            raise PermissionError('Explicit local-owner confirmation required.')
        apps=self._apps()
        config={'service':service,'port':port,'path':path}
        if app in apps and apps[app]!=config:
            raise RuntimeError('Cannot silently replace existing app policy.')
        apps[app]=config
        secure_write(self.policy,json.dumps(apps,indent=2)+'\n')
        return config

    def _root(self,app):
        if not isinstance(app,str) or not APP.fullmatch(app) or app not in self._apps():
            raise PermissionError('Application not approved on this machine.')
        root=self.state/'deployments'/app
        for part in (self.state/'deployments',root,root/'releases'):
            if part.is_symlink():raise RuntimeError('Symlinked deployment directory refused.')
            part.mkdir(mode=0o700,parents=True,exist_ok=True)
            os.chmod(part,0o700)
        return root

    def _signed(self,bundle,signature,signer,digest):
        bundle,signature=Path(bundle),Path(signature)
        if self.signers.is_symlink() or not self.signers.is_file():
            raise RuntimeError('Approved offline signer file is not installed.')
        if not isinstance(signer,str) or not SIGNER.fullmatch(signer):
            raise ValueError('Invalid release signer.')
        if not isinstance(digest,str) or not DIGEST.fullmatch(digest):
            raise ValueError('Invalid pinned release digest.')
        if any(p.is_symlink() or not p.is_file() for p in (bundle,signature)):
            raise RuntimeError('Missing/unsafe signature or bundle.')
        if bundle.stat().st_size>MAX_ARCHIVE or signature.stat().st_size>10000:
            raise RuntimeError('Release payload too large.')
        data=bundle.read_bytes()
        if hashlib.sha256(data).hexdigest()!=digest:
            raise RuntimeError('Release SHA-256 mismatch.')
        try:
            result=subprocess.run(['ssh-keygen','-Y','verify','-f',str(self.signers),
                '-I',signer,'-n','open-ci-hub-deploy','-s',str(signature)],
                input=data,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                timeout=20,check=False)
        except (OSError,subprocess.TimeoutExpired):
            raise RuntimeError('Release signature verifier unavailable.') from None
        if result.returncode:
            raise RuntimeError('Release was not signed by an approved external signer.')
        return data

    @staticmethod
    def _manifest(obj,app):
        if not isinstance(obj,dict) or obj.get('application')!=app or not COMMIT.fullmatch(str(obj.get('source_commit',''))):
            raise RuntimeError('Signed release metadata mismatch.')
        checks=obj.get('checks')
        if not isinstance(checks,dict) or any(
            not isinstance(checks.get(key),dict) or checks[key].get('status')!='passed' or
            not DIGEST.fullmatch(str(checks[key].get('log_sha256','')))
            for key in REQUIRED):
            raise RuntimeError('Signed release has no full tests/build/database checklist.')
        # Human release signer approval is still not independent executor attestation.
        if obj.get('reviewed_by_signer') is not True:
            raise RuntimeError('Release signer review must be explicit.')
        return obj

    def stage(self,app,bundle,signature,signer,digest,confirm=False):
        if confirm is not True:raise PermissionError('Explicit release staging confirmation required.')
        root=self._root(app)
        raw=self._signed(bundle,signature,signer,digest)
        dest=root/'releases'/digest
        if dest.exists() or dest.is_symlink():
            raise RuntimeError('Existing immutable release cannot be overwritten.')
        folder=Path(tempfile.mkdtemp(prefix='.stage-',dir=root/'releases'))
        manifest=None
        try:
            files={};total=0
            with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
                entries=archive.getmembers()
                if len(entries)>1000:raise RuntimeError('Too many archive entries.')
                for member in entries:
                    path=PurePosixPath(member.name)
                    if not member.name or path.is_absolute() or '..' in path.parts or member.name.startswith('-') or member.name==INVENTORY:
                        raise RuntimeError('Unsafe archive member name.')
                    target=folder.joinpath(*path.parts)
                    if member.isdir():
                        target.mkdir(mode=0o700,parents=True,exist_ok=True)
                        continue
                    if not member.isfile() or member.size>MAX_ARCHIVE or member.name in files:
                        raise RuntimeError('Special archive member, duplicate or oversized file.')
                    total+=member.size
                    if total>MAX_UNPACKED:raise RuntimeError('Unpacked archive is too large.')
                    target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                    digest_file=hashlib.sha256()
                    with archive.extractfile(member) as source, target.open('xb') as output:
                        while True:
                            piece=source.read(65536)
                            if not piece:break
                            output.write(piece);digest_file.update(piece)
                    os.chmod(target,0o700 if member.mode & 0o111 else 0o600)
                    files[member.name]=digest_file.hexdigest()
            if 'release.json' not in files:raise RuntimeError('Signed release.json absent.')
            try:manifest=json.loads((folder/'release.json').read_text(encoding='utf-8'))
            except (ValueError,UnicodeError):
                raise RuntimeError('Invalid signed release.json.') from None
            self._manifest(manifest,app)
            secure_write(folder/INVENTORY,json.dumps({'bundle_sha256':digest,
                'signer':signer,'source_commit':manifest['source_commit'],
                'files':files},sort_keys=True)+'\n')
            os.rename(folder,dest)
        finally:
            if folder.exists():shutil.rmtree(folder)
        return {'application':app,'release':digest,'state':'staged',
                'source_commit':manifest['source_commit'], 'reviewer_signed':True,
                'independent_execution_attestation':False}

    def _verify(self,root,digest):
        if not isinstance(digest,str) or not DIGEST.fullmatch(digest):
            raise ValueError('Invalid release digest.')
        folder=root/'releases'/digest
        if folder.is_symlink() or not folder.is_dir() or (folder/INVENTORY).is_symlink():
            raise RuntimeError('Release files unavailable or unsafe.')
        try:info=json.loads((folder/INVENTORY).read_text(encoding='utf-8'))
        except (OSError,ValueError):
            raise RuntimeError('Release inventory missing or corrupt.') from None
        if info.get('bundle_sha256')!=digest or not isinstance(info.get('files'),dict):
            raise RuntimeError('Release digest mismatch.')
        for item,expected in info['files'].items():
            path=PurePosixPath(item)
            if path.is_absolute() or '..' in path.parts or not DIGEST.fullmatch(str(expected)):
                raise RuntimeError('Invalid release inventory.')
            file=folder.joinpath(*path.parts)
            if file.is_symlink() or not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=expected:
                raise RuntimeError('Staged file modified after signature verification.')
        found={str(p.relative_to(folder)) for p in folder.rglob('*') if p.is_file()}
        if found!=set(info['files'])|{INVENTORY}:
            raise RuntimeError('Unexpected unverified files in signed release.')
        return info

    @staticmethod
    def _current(root,name):
        file=root/name
        if not file.is_symlink():
            if file.exists():raise RuntimeError('Unexpected non-symlink release pointer.')
            return None
        target=os.readlink(file)
        if not target.startswith('releases/') or not DIGEST.fullmatch(target[9:]):
            raise RuntimeError('Unsafe release pointer.')
        return target[9:]

    def _point(self,root,name,digest):
        pointer=root/name
        if self._current(root,name)==digest:return
        temp=root/('.pointer-'+secrets.token_hex(6))
        os.symlink('releases/'+digest,temp)
        try:os.replace(temp,pointer)
        finally:
            if temp.is_symlink():temp.unlink()

    @staticmethod
    def _restart(service):
        try:
            r=subprocess.run(['/usr/bin/systemctl','--user','--no-ask-password','restart',service],
                             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=40,check=False)
        except (OSError,subprocess.TimeoutExpired):
            raise RuntimeError('Cannot restart user application service.') from None
        if r.returncode:raise RuntimeError('Service restart failed.')

    @staticmethod
    def _health(port,path):
        try:
            with http.client.HTTPConnection('127.0.0.1',port,timeout=5) as conn:
                conn.request('GET',path,headers={'Accept':'application/json'})
                response=conn.getresponse()
                response.read(2048)
                if response.status!=200:raise RuntimeError('Local health check returned non-200.')
        except (OSError,TimeoutError,http.client.HTTPException):
            raise RuntimeError('Local application health probe failed.') from None

    def deploy(self,app,digest,confirm=False):
        if confirm is not True:raise PermissionError('Explicit deployment confirmation required.')
        config=self._apps().get(app)
        root=self._root(app)
        self._verify(root,digest)
        old=self._current(root,'current')
        if old==digest:return {'state':'already-active','release':digest}
        self._point(root,'current',digest)
        try:
            self._restart(config['service'])
            self._health(config['port'],config['path'])
        except RuntimeError as err:
            if old:
                self._point(root,'current',old)
                try:self._restart(config['service'])
                except RuntimeError:pass
            else:(root/'current').unlink(missing_ok=True)
            raise RuntimeError('Deployment failed and previous pointer restored: '+str(err)) from None
        if old:self._point(root,'previous',old)
        return {'state':'healthy','release':digest,'previous':old}

    def rollback(self,app,confirm=False):
        if confirm is not True:raise PermissionError('Explicit rollback confirmation required.')
        root=self._root(app)
        old=self._current(root,'previous')
        if not old:raise RuntimeError('No earlier release is available.')
        return self.deploy(app,old,confirm=True)

    def status(self,app):
        root=self._root(app)
        return {'app':app,'current':self._current(root,'current'),
                'previous':self._current(root,'previous')}

def main(argv=None):
    if os.name!='posix' or os.geteuid()==0:
        print('Only non-root Linux user deployments permitted.',file=sys.stderr)
        return 1
    cli=argparse.ArgumentParser(description=__doc__)
    sub=cli.add_subparsers(dest='action',required=True)
    a=sub.add_parser('allow');a.add_argument('app');a.add_argument('service')
    a.add_argument('--health-port',type=int,required=True);a.add_argument('--health-path',default='/health')
    a.add_argument('--confirm',action='store_true')
    b=sub.add_parser('stage');b.add_argument('app');b.add_argument('bundle');b.add_argument('signature')
    b.add_argument('--sha256',required=True);b.add_argument('--signer',required=True)
    b.add_argument('--confirm',action='store_true')
    for kind in ('deploy','rollback','status'):
        cmd=sub.add_parser(kind);cmd.add_argument('app')
        if kind=='deploy':cmd.add_argument('digest')
        if kind!='status':cmd.add_argument('--confirm',action='store_true')
    v=cli.parse_args(argv)
    try:
        mgr=Releases()
        if v.action=='allow':value=mgr.allow(v.app,v.service,v.health_port,v.health_path,v.confirm)
        elif v.action=='stage':value=mgr.stage(v.app,v.bundle,v.signature,v.signer,v.sha256,v.confirm)
        elif v.action=='deploy':value=mgr.deploy(v.app,v.digest,v.confirm)
        elif v.action=='rollback':value=mgr.rollback(v.app,v.confirm)
        else:value=mgr.status(v.app)
        print(json.dumps(value,indent=2))
        return 0
    except (RuntimeError,ValueError,PermissionError,OSError,TypeError) as exc:
        print('Release manager:',exc,file=sys.stderr)
        return 1
if __name__=='__main__':
    raise SystemExit(main())
