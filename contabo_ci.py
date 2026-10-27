#!/usr/bin/env python3
"""Execute a trusted, pinned Open CI Hub main snapshot in isolated Contabo CPU.

Never accepts arbitrary shell commands, provider credentials, custom repos,
deployment tasks or mutable workspaces. Remote worker receives read-only source
bytes, revalidates archive paths, then runs one fixed unittest discovery command
as a systemd DynamicUser with no network or access to production services.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import tempfile
import time

MAX_ARCHIVE=12_000_000
MAX_EXTRACT=30_000_000
MAX_MEMBERS=700
MAX_HEADER=512
SHA=re.compile(r"^[a-f0-9]{40}$")
REMOTE="/usr/local/lib/open-ci-hub/contabo_ci.py"
REPOSITORY="Raebu/open_ci_hub"

def _reject_file(name,member):
    p=PurePosixPath(name)
    if (p.is_absolute() or not p.parts or p.parts[0]!="snapshot"
        or len(p.parts)>14 or any(s in (".","..","") for s in p.parts)
        or ".git" in p.parts
        or member.issym() or member.islnk() or member.isdev()
        or not (member.isfile() or member.isdir())):
        raise ValueError("Untrusted archive entry.")
    return p.parts[1:]

def _unpack(src,destination):
    count,total=0,0
    with tarfile.open(fileobj=io.BytesIO(src),mode="r:") as tf:
        for m in tf:
            count+=1
            if count>MAX_MEMBERS:
                raise ValueError("Too many source entries.")
            parts=_reject_file(m.name,m)
            if not parts:
                if not m.isdir():
                    raise ValueError("Expected source directory.")
                continue
            target=destination.joinpath(*parts)
            if m.isdir():
                target.mkdir(mode=0o700,parents=True,exist_ok=True)
                continue
            total+=m.size
            if m.size<0 or total>MAX_EXTRACT:
                raise ValueError("Source extraction size exceeded.")
            target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            try:
                source=tf.extractfile(m)
                if source is None:
                    raise ValueError("Missing archive body.")
                with os.fdopen(fd,"wb") as output:
                    remaining=m.size
                    while remaining:
                        buf=source.read(min(65536,remaining))
                        if not buf:
                            raise ValueError("Truncated archive.")
                        output.write(buf);remaining-=len(buf)
            except Exception:
                # os.fdopen owns fd only after entered; unit state is disposable.
                raise
    if not (destination/"tests").is_dir() or not (destination/"app_runtime.py").is_file():
        raise ValueError("Snapshot is not an Open CI Hub test source tree.")
    return count

def worker():
    wire=sys.stdin.buffer.read(MAX_ARCHIVE+MAX_HEADER+5)
    try:
        if len(wire)>MAX_ARCHIVE+MAX_HEADER+4 or len(wire)<6:
            raise ValueError("Invalid archive transfer size.")
        n=int.from_bytes(wire[:4],"big")
        if n<10 or n>MAX_HEADER or 4+n>=len(wire):
            raise ValueError("Invalid transfer metadata.")
        header=json.loads(wire[4:4+n]);archive=wire[4+n:]
        if (not isinstance(header,dict) or set(header)!={"sha","archive_sha256"}
            or not isinstance(header["sha"],str) or not SHA.fullmatch(header["sha"])
            or not isinstance(header["archive_sha256"],str)
            or len(header["archive_sha256"])!=64
            or hashlib.sha256(archive).hexdigest()!=header["archive_sha256"]):
            raise ValueError("Archive hash mismatch.")
        with tempfile.TemporaryDirectory(prefix="oci-contabo-ci-") as tmp:
            root=Path(tmp)/"source"
            root.mkdir(mode=0o700)
            count=_unpack(archive,root)
            if count<50:
                raise ValueError("Unexpectedly small source tree.")
            start=time.monotonic()
            env={"HOME":tmp,"LANG":"C.UTF-8","PYTHONNOUSERSITE":"1",
                 "PYTHONDONTWRITEBYTECODE":"1","PATH":"/usr/bin:/bin"}
            # -I blocks PYTHONPATH, but unittest needs this temporary
            # checkout explicitly on sys.path to import project modules.
            fixed_driver=(
                "import os,sys,unittest;"
                "sys.path.insert(0,os.getcwd());"
                "suite=unittest.defaultTestLoader.discover('tests');"
                "result=unittest.TextTestRunner(verbosity=0).run(suite);"
                "sys.exit(not result.wasSuccessful())"
            )
            run=subprocess.run(
                ["/usr/bin/python3","-I","-c",fixed_driver],
                cwd=root,env=env,stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                timeout=115,check=False)
            combined=(run.stdout+run.stderr)[-4000:]
            match=re.search(rb"Ran ([0-9]+) tests in ",combined)
            tests=int(match.group(1)) if match else 0
            # Do not treat zero, missing-dependency or truncated suites as success.
            success=run.returncode==0 and tests>=800
            report={"success":success,"sha":header["sha"],"tests":tests,
                    "archive_sha256":header["archive_sha256"],
                    "duration_seconds":round(time.monotonic()-start,2),
                    "runner":"contabo_ephemeral_offline_dynamic_user",
                    "source_entries":count}
            print(json.dumps(report,sort_keys=True,separators=(",",":")))
            return 0 if success else 2
    except (ValueError,KeyError,TypeError,OSError,tarfile.TarError,subprocess.TimeoutExpired):
        print('{"success":false,"reason":"invalid_or_unverified_test_snapshot"}')
        return 2

def _git(root,*args):
    proc=subprocess.run(["git","-C",str(root),*args],capture_output=True,
                        timeout=16,check=False)
    if proc.returncode:
        raise RuntimeError("Git provenance unavailable.")
    return proc.stdout.decode().strip()

def remote_argv():
    return ["ssh","-T","-o","BatchMode=yes","-o","StrictHostKeyChecking=yes",
            "-o","ConnectTimeout=7","contabo-talent",
            "systemd-run","--quiet","--wait","--pipe","--collect",
            "--property=DynamicUser=yes","--property=PrivateNetwork=yes",
            "--property=PrivateTmp=yes","--property=ProtectSystem=strict",
            "--property=ProtectHome=yes","--property=NoNewPrivileges=yes",
            "--property=CapabilityBoundingSet=",
            "--property=RestrictAddressFamilies=AF_UNIX",
            "--property=RestrictSUIDSGID=yes",
            "--property=ProtectKernelTunables=yes",
            "--property=ProtectControlGroups=yes",
            "--property=ProtectKernelModules=yes",
            "--property=ProtectKernelLogs=yes",
            "--property=PrivateDevices=yes","--property=LockPersonality=yes",
            "--property=RestrictRealtime=yes","--property=MemoryMax=1250M",
            "--property=CPUQuota=150%","--property=TasksMax=128",
            "--property=RuntimeMaxSec=145s",
            "--","/usr/bin/python3","-I",REMOTE,"--worker"]

def dispatch(root):
    root=Path(root).resolve()
    if _git(root,"branch","--show-current")!="main":
        raise RuntimeError("Only the trusted main branch may be tested remotely.")
    if _git(root,"status","--porcelain"):
        raise RuntimeError("Uncommitted work must not be included.")
    head=_git(root,"rev-parse","HEAD")
    remote=_git(root,"ls-remote","origin","refs/heads/main").split()[0]
    if head!=remote or not SHA.fullmatch(head):
        raise RuntimeError("Source SHA is not current owner main.")
    tar=subprocess.run(["git","-C",str(root),"archive","--format=tar",
                        "--prefix=snapshot/","HEAD"],
                       capture_output=True,timeout=15,check=False)
    if tar.returncode or not 1000<len(tar.stdout)<=MAX_ARCHIVE:
        raise RuntimeError("Owner source archive rejected.")
    digest=hashlib.sha256(tar.stdout).hexdigest()
    meta=json.dumps({"sha":head,"archive_sha256":digest},separators=(",",":")).encode()
    wire=len(meta).to_bytes(4,"big")+meta+tar.stdout
    try:
        p=subprocess.run(remote_argv(),input=wire,capture_output=True,
                         timeout=175,check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Remote sandbox timed out; no verified result.") from None
    if p.returncode or len(p.stdout)>2048:
        raise RuntimeError("Contabo test sandbox reported failure; not production proof.")
    try:
        result=json.loads(p.stdout)
    except (TypeError,ValueError):
        raise RuntimeError("Contabo did not return a valid receipt.") from None
    if (not isinstance(result,dict) or result.get("success") is not True
        or result.get("sha")!=head or result.get("archive_sha256")!=digest
        or type(result.get("tests")) is not int or result["tests"]<800):
        raise RuntimeError("Remote test receipt failed provenance or completeness.")
    return result

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--worker",action="store_true")
    group.add_argument("--trusted-main",action="store_true")
    args=parser.parse_args(argv)
    if args.worker:
        return worker()
    try:
        print(json.dumps(dispatch(Path(__file__).resolve().parent),sort_keys=True))
        return 0
    except (RuntimeError,OSError,ValueError) as e:
        print(json.dumps({"success":False,"reason":str(e)[:140]}))
        return 2

if __name__=="__main__":
    raise SystemExit(main())
