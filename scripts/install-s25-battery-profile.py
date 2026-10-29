#!/usr/bin/env python3
"""One-time, reviewed S25 Termux worker update for safe 20% battery reserve.

Run explicitly in Termux ON THE S25, never on the Razer or S22.
Installs only the two allowlisted worker source files, keeps private backups,
adds ONE opt-in environment variable to the EXISTING runit service, and asks
runit to restart that service. Does not touch OAuth, worker tokens, Tailscale,
Termux:Boot, global Android settings, contacts or system firmware.

The sources are downloaded from the existing public Raeburn GitHub repository,
then checked against the exact reviewed SHA-256 hashes below. Changed or
untrusted upstream code is refused.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

SOURCE_REPO = "https://raw.githubusercontent.com/Raebu/open_ci_hub/main/"
SOURCES = {
    "edge_worker.py": "31bd07580bff9908868e80f97a90db6da73357bb02d8a85569d97d1c182c8ecb",
    "application_client.py": "78f1f9986b9a72700c9ff887c2882a447d7e34828fe7f750078490cc5812f7a5",
}
TAG = "export OCI_EDGE_POWER_PROFILE=s25-edge"
SERVICE = "oci-edge-worker"


class Refused(ValueError):
    pass


def phone_check(*,prefix=None,getprop=subprocess.run):
    prefix = Path(prefix if prefix is not None else os.environ.get("PREFIX",""))
    if not str(prefix).startswith("/data/data/com.termux/files/usr"):
        raise Refused("Run only inside your S25 Termux application.")
    try:
        result = getprop(["/system/bin/getprop","ro.product.model"],
                         capture_output=True,text=True,timeout=6,check=False)
    except (OSError,subprocess.TimeoutExpired):
        raise Refused("Could not independently verify Galaxy S25 model.") from None
    model = result.stdout.strip() if result.returncode == 0 else ""
    if not re.fullmatch(r"SM-S93[0-9][A-Za-z0-9-]{0,12}",model):
        raise Refused("Device is not a recognised Galaxy S25 family model.")
    if os.geteuid() == 0:
        raise Refused("Refusing elevated/root installation.")
    return model


def find_worker(home: Path,run_text: str):
    if "edge_worker.py" not in run_text:
        raise Refused("Existing service does not reference a trusted edge worker.")
    candidates=[]
    # Inspect only the Termux user's home, never Android shared storage.
    for path in home.rglob("edge_worker.py"):
        if (not path.is_file() or path.is_symlink()
            or not path.parent.is_relative_to(home)
            or path.parent.is_symlink()):
            continue
        client=path.parent/"application_client.py"
        if not client.is_file() or client.is_symlink():
            continue
        worker_text=path.read_text(encoding="utf8",errors="replace")
        cli_text=client.read_text(encoding="utf8",errors="replace")
        if ("def phone_ready" in worker_text and "MIN_BATTERY_PERCENT" in worker_text
            and "class ApplicationClient" in cli_text):
            candidates.append(path.parent)
        if len(candidates)>10:
            raise Refused("Too many worker checkouts; refusing to guess.")
    if len(candidates)!=1:
        raise Refused("Cannot uniquely locate existing worker checkout in Termux home.")
    return candidates[0]


def read_pinned(name,*,urlopen=urllib.request.urlopen):
    if name not in SOURCES:
        raise Refused("Only reviewed worker/client sources may be installed.")
    try:
        with urlopen(SOURCE_REPO+name,timeout=15) as response:
            contents=response.read(65000)
    except (OSError,ValueError,TimeoutError):
        raise Refused("Could not retrieve the reviewed GitHub file.") from None
    if len(contents)>60000 or hashlib.sha256(contents).hexdigest()!=SOURCES[name]:
        raise Refused("GitHub file SHA-256 mismatch: refusing unreviewed code.")
    try:
        compile(contents.decode("utf8"),name,"exec")
    except (UnicodeError,SyntaxError):
        raise Refused("Reviewed worker file failed Python syntax validation.") from None
    return contents


def atomic_write(path: Path,contents: bytes,mode=0o600):
    if path.is_symlink() or not path.is_file():
        raise Refused("Worker target unavailable or symbolic link.")
    fd,name=tempfile.mkstemp(prefix=".s25-update-",dir=path.parent)
    try:
        with os.fdopen(fd,"wb") as file:
            file.write(contents)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(name,mode)
        os.replace(name,path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def apply_update(*,prefix=None,home=None,
                 getprop=subprocess.run,urlopen=urllib.request.urlopen,
                 restart=subprocess.run,commit=False):
    if not commit:
        raise Refused("Run with --install to approve this local S25-only change.")
    model=phone_check(prefix=prefix,getprop=getprop)
    prefix=Path(prefix if prefix is not None else os.environ["PREFIX"])
    home=Path(home if home is not None else os.environ["HOME"])
    if home.is_symlink() or not home.is_dir():
        raise Refused("Termux home location unsafe.")
    runner=prefix/"var/service"/SERVICE/"run"
    if not runner.exists() or runner.is_symlink() or not runner.is_file():
        raise Refused("Cannot find existing oci-edge-worker runit launcher.")
    if not runner.resolve().is_relative_to(prefix) and not runner.resolve().is_relative_to(home):
        raise Refused("Worker launcher outside Termux private storage.")
    existing=runner.read_text(encoding="utf8")
    if not existing.startswith("#!"):
        raise Refused("Existing worker launcher has no shebang.")
    directory=find_worker(home,existing)
    new={name:read_pinned(name,urlopen=urlopen) for name in SOURCES}
    current={name:(directory/name).read_bytes() for name in SOURCES}
    already=(all(current[name]==new[name] for name in SOURCES) and
             TAG in existing.splitlines())
    if already:
        return {"model":model,"state":"already_configured",
                "reserve_percent":20,"service_restart":"not_needed"}
    if TAG in existing and existing.splitlines().count(TAG)!=1:
        raise Refused("Conflicting existing battery power-profile settings.")
    if "OCI_EDGE_POWER_PROFILE=" in existing and TAG not in existing:
        raise Refused("Another battery policy exists; refusing silent override.")
    # Back up all three files BEFORE modifying any. This includes a service
    # runner that might contain credentials; backups never leave Termux HOME.
    targets=[directory/name for name in SOURCES]+[runner]
    for target in targets:
        backup=target.with_name(target.name+".before-s25-20pct")
        if backup.is_symlink():
            raise Refused("Unsafe worker backup path.")
        if not backup.exists():
            fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,"wb") as out:
                out.write(target.read_bytes())
                out.flush()
                os.fsync(out.fileno())
        elif not backup.is_file():
            raise Refused("Unexpected existing worker backup.")
    # Change only selected public code files, never credentials.
    for name,contents in new.items():
        target=directory/name
        if current[name]!=contents:
            atomic_write(target,contents)
    if TAG not in existing:
        lines=existing.splitlines(keepends=True)
        insert=TAG+"\n"
        patched=lines[0]+insert+"".join(lines[1:])
        atomic_write(runner,patched.encode(),mode=0o700)
    # Safe restart of the existing runit service. No new background daemon.
    try:
        result=restart(["sv","restart",SERVICE],
                       stdin=subprocess.DEVNULL,capture_output=True,
                       timeout=20,check=False)
        status="restart_requested" if result.returncode==0 else "restart_requires_manual_check"
    except (OSError,subprocess.TimeoutExpired):
        status="restart_requires_manual_check"
    return {"model":model,"state":"installed","reserve_percent":20,
            "charging_required":False,"service_restart":status,
            "backup_scope":"private_Termux_files_only",
            "note":"Verify real Hub online/heartbeat before claiming connectivity."}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--install",action="store_true")
    opts=p.parse_args(argv)
    try:
        result=apply_update(commit=opts.install)
        import json
        print(json.dumps(result,sort_keys=True,indent=2))
        return 0
    except (Refused,PermissionError,OSError,UnicodeError) as e:
        print("S25 profile update blocked: "+str(e)[:160],file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
