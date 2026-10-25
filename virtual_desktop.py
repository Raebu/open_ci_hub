#!/usr/bin/env python3
"""Private, opt-in Xvfb desktop separate from KDE. No arbitrary remote commands.

Safe to install as source before the official xorg-server-xvfb package exists.
Never touches DISPLAY, KDE services, X11 authority or running user windows.
"""
from __future__ import annotations
import argparse
import os
import hashlib
import stat
from pathlib import Path
import secrets
import subprocess
import time
from autonomy_core import admit_local,sample_host

ROOT=Path(__file__).resolve().parent
BASE=ROOT/".local"/"virtual-desktop"
BINARY=Path("/usr/bin/Xvfb")
USER_BINARY=Path.home()/".local/opt/open-ci-hub/xvfb-21.1.25-2/usr/bin/Xvfb"
# Extracted from Arch's signed official xorg-server-xvfb 21.1.25-2 package.
USER_BINARY_SHA256="6584e6cf04ca1357cb50fd2feb1ad449b5f45dfe6cc61d2bbf006f752df592de"
UNIT="open-ci-hub-virtual-display.service"
DISPLAY=":91"

def selected_binary():
    """Prefer distro package; accept only our signature-reviewed pinned user binary."""
    if BINARY.is_file() and os.access(BINARY,os.X_OK):
        return BINARY
    candidate=USER_BINARY
    try:
        if candidate.is_symlink() or not candidate.is_file():
            return None
        info=candidate.stat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or
            info.st_mode & 0o022 or not os.access(candidate,os.X_OK)):
            return None
        with candidate.open("rb") as handle:
            if hashlib.file_digest(handle,"sha256").hexdigest()!=USER_BINARY_SHA256:
                return None
        return candidate
    except (OSError,PermissionError):
        return None

def create_authority(path,display,token):
    """Use trusted xauth, passing the cookie via stdin rather than process args."""
    if path.is_symlink() or path.exists() or len(token)!=16 or display!=":91":
        raise ValueError("Unsafe X11 authorization request.")
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    os.close(fd)
    try:
        result=subprocess.run(["/usr/bin/xauth","-q","-f",str(path)],
            input="add "+display+" MIT-MAGIC-COOKIE-1 "+token.hex()+"\n",
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            text=True,timeout=5,check=False)
        if result.returncode!=0 or path.stat().st_size<=0:
            raise OSError("Failed to create private MIT-MAGIC-COOKIE authority.")
        os.chmod(path,0o600)
    except Exception:
        path.unlink(missing_ok=True)
        raise

def _systemctl(*args):
    return subprocess.run(["/usr/bin/systemctl","--user","--no-ask-password",*args],
            capture_output=True,text=True,stdin=subprocess.DEVNULL,timeout=8,check=False)

def status():
    state=_systemctl("is-active",UNIT)
    socket_path=Path("/tmp/.X11-unix/X91")
    return {"active":state.returncode==0 and state.stdout.strip()=="active",
            "display":DISPLAY,"socket_present":socket_path.exists(),
            "binary_available":selected_binary() is not None,
            "binary_source":("system" if BINARY.is_file() else
                             "signed_user_package" if selected_binary() else "missing"),
            "type":"separate_virtual_X11_framebuffer_not_KDE"}

def start(*,confirm=False):
    if confirm is not True:
        raise PermissionError("Explicit owner consent required.")
    ok,reason=admit_local(sample_host())
    if not ok:
        return {"started":False,"reason":reason}
    binary=selected_binary()
    if binary is None:
        return {"started":False,"reason":"verified_xvfb_binary_unavailable"}
    if status()["active"] or Path("/tmp/.X11-unix/X91").exists():
        return {"started":False,"reason":"display_already_in_use"}
    BASE.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(BASE,0o700)
    key=BASE/"Xauthority"
    if key.is_symlink() or key.exists():
        return {"started":False,"reason":"authority_file_already_exists"}
    create_authority(key,DISPLAY,secrets.token_bytes(16))
    command=["/usr/bin/systemd-run","--user","--collect",
             "--unit="+UNIT.removesuffix(".service"),
             "-p","MemoryMax=512M","-p","MemorySwapMax=0",
             "-p","CPUQuota=40%","-p","TasksMax=128","-p","IOWeight=15",
             "--",str(binary),DISPLAY,"-screen","0","1280x720x24",
             "-nolisten","tcp","-auth",str(key),"-noreset"]
    try:
        proc=subprocess.run(command,stdin=subprocess.DEVNULL,
                    capture_output=True,text=True,check=False,timeout=12)
        if proc.returncode:
            key.unlink(missing_ok=True)
            return {"started":False,"reason":"transient_service_refused"}
        for _ in range(20):
            if status()["active"] and status()["socket_present"]:
                return {"started":True,"display":DISPLAY,
                        "authority":"private_file_in_state_folder"}
            time.sleep(.1)
        return {"started":False,"reason":"display_did_not_start"}
    except (OSError,subprocess.TimeoutExpired):
        return {"started":False,"reason":"transient_service_unavailable"}

def stop(*,confirm=False):
    if confirm is not True:
        raise PermissionError("Explicit owner confirmation required.")
    result=_systemctl("stop",UNIT)
    now=status()
    if now["active"]:
        return {"stopped":False,"reason":"service_still_active"}
    # Do not touch auth cookie unless unit is proven stopped.
    key=BASE/"Xauthority"
    if not key.is_symlink():
        key.unlink(missing_ok=True)
    return {"stopped":True,"display":DISPLAY}

def main(argv=None):
    import json
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=("check","start","stop"))
    parser.add_argument("--confirm",action="store_true")
    a=parser.parse_args(argv)
    try:
        result=status() if a.command=="check" else (
            start(confirm=a.confirm) if a.command=="start" else stop(confirm=a.confirm))
        print(json.dumps(result,indent=2))
        return 0 if a.command=="check" or result.get("started",result.get("stopped",False)) else 2
    except (PermissionError,OSError,ValueError) as e:
        print("Virtual desktop refused: "+str(e)[:100])
        return 2

if __name__=="__main__":
    raise SystemExit(main())
