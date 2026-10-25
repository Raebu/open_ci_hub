#!/usr/bin/env python3
"""Private, opt-in Xvfb desktop separate from KDE. No arbitrary remote commands.

Safe to install as source before the official xorg-server-xvfb package exists.
Never touches DISPLAY, KDE services, X11 authority or running user windows.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import time
from autonomy_core import admit_local,sample_host

ROOT=Path(__file__).resolve().parent
BASE=ROOT/".local"/"virtual-desktop"
BINARY=Path("/usr/bin/Xvfb")
UNIT="open-ci-hub-virtual-display.service"
DISPLAY=":91"

def xauthority(display,token):
    def part(raw):
        return struct.pack(">H",len(raw))+raw
    if len(token)!=16 or not display.startswith(":"):
        raise ValueError("Invalid X11 authority.")
    # Local family 256, empty address, display 91, 16-byte MIT-MAGIC-COOKIE-1.
    return struct.pack(">H",256)+part(b"")+part(display[1:].encode())+part(
        b"MIT-MAGIC-COOKIE-1")+part(token)

def _systemctl(*args):
    return subprocess.run(["/usr/bin/systemctl","--user","--no-ask-password",*args],
            capture_output=True,text=True,stdin=subprocess.DEVNULL,timeout=8,check=False)

def status():
    state=_systemctl("is-active",UNIT)
    socket_path=Path("/tmp/.X11-unix/X91")
    return {"active":state.returncode==0 and state.stdout.strip()=="active",
            "display":DISPLAY,"socket_present":socket_path.exists(),
            "binary_available":BINARY.is_file(),
            "type":"separate_virtual_X11_framebuffer_not_KDE"}

def start(*,confirm=False):
    if confirm is not True:
        raise PermissionError("Explicit owner consent required.")
    ok,reason=admit_local(sample_host())
    if not ok:
        return {"started":False,"reason":reason}
    if not BINARY.is_file():
        return {"started":False,"reason":"official_xorg_server_xvfb_not_installed"}
    if status()["active"] or Path("/tmp/.X11-unix/X91").exists():
        return {"started":False,"reason":"display_already_in_use"}
    BASE.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(BASE,0o700)
    key=BASE/"Xauthority"
    if key.is_symlink() or key.exists():
        return {"started":False,"reason":"authority_file_already_exists"}
    fd=os.open(key,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,"wb") as handle:
            handle.write(xauthority(DISPLAY,secrets.token_bytes(16)))
    except Exception:
        key.unlink(missing_ok=True)
        raise
    command=["/usr/bin/systemd-run","--user","--collect",
             "--unit="+UNIT.removesuffix(".service"),
             "-p","MemoryMax=512M","-p","MemorySwapMax=0",
             "-p","CPUQuota=40%","-p","TasksMax=128","-p","IOWeight=15",
             "--",str(BINARY),DISPLAY,"-screen","0","1280x720x24",
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
