#!/usr/bin/env python3
"""Owner-controlled, idempotent, persistent Open CI Hub dashboard user service.

No root, Tailscale Funnel, desktop session secret, credential changes, CI worker
restart, network expansion or deletion of another application's systemd unit.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
NAME="open-ci-hub-dashboard.service"

def unit_text(root):
    root=Path(root).resolve(strict=True)
    if not root.is_dir() or not re.fullmatch(r"/[A-Za-z0-9_./-]+",str(root)):
        raise ValueError("Unsupported checkout path for literal systemd WorkingDirectory.")
    if not (root/"hub.py").is_file() or not (root/"web_console.py").is_file():
        raise ValueError("Not an Open CI Hub source checkout.")
    return (f"[Unit]\n"
            f"Description=Open CI Hub authenticated private dashboard\n"
            f"After=network.target\n"
            f"StartLimitIntervalSec=120\n"
            f"StartLimitBurst=5\n"
            f"[Service]\n"
            f"Type=exec\n"
            f"WorkingDirectory={root}\n"
            f"ExecStart=/usr/bin/python3 -u hub.py dashboard --no-browser\n"
            f"Restart=on-failure\n"
            f"RestartSec=5\n"
            f"NoNewPrivileges=true\n"
            f"PrivateTmp=true\n"
            f"UMask=0077\n"
            f"Nice=10\n"
            f"MemoryMax=768M\n"
            f"CPUQuota=60%\n"
            f"TasksMax=128\n"
            f"[Install]\n"
            f"WantedBy=default.target\n")

def _run(*args):
    try:
        p=subprocess.run(args,stdin=subprocess.DEVNULL,capture_output=True,
                         text=True,timeout=15,check=False)
    except (OSError,subprocess.TimeoutExpired):
        raise RuntimeError("User service manager unavailable.") from None
    if p.returncode:
        raise RuntimeError("Systemd user service operation failed.")
    return p.stdout.strip()

def install(*,root=ROOT,home=None,confirmed=False):
    if confirmed is not True:
        raise PermissionError("Explicit owner confirmation (--confirm) required.")
    if not hasattr(os,"getuid") or os.getuid()==0:
        raise PermissionError("Only an unprivileged desktop user may install.")
    root=Path(root).resolve(strict=True)
    expected=unit_text(root)
    if _run("git","-C",str(root),"branch","--show-current")!="main":
        raise RuntimeError("Install from the reviewed main checkout only.")
    if _run("git","-C",str(root),"status","--porcelain","--untracked-files=no"):
        raise RuntimeError("Tracked source is not clean; refusing to install.")
    folder=Path(home) if home is not None else Path.home()
    unit_dir=folder/".config"/"systemd"/"user"
    if unit_dir.is_symlink():
        raise RuntimeError("Symlinked unit directory refused.")
    unit_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
    dest=unit_dir/NAME
    if dest.is_symlink():
        raise RuntimeError("Symlinked unit file refused.")
    if dest.exists():
        if not dest.is_file() or dest.read_text()!=expected:
            raise RuntimeError("Existing dashboard unit differs; refusing overwrite.")
        action="already_installed"
    else:
        old=os.umask(0o077)
        try:
            with dest.open("x",encoding="utf-8") as out:
                out.write(expected)
            dest.chmod(0o600)
        finally:
            os.umask(old)
        action="created"
    _run("systemctl","--user","daemon-reload")
    _run("systemctl","--user","enable","--now",NAME)
    if _run("systemctl","--user","is-active",NAME)!="active":
        raise RuntimeError("Dashboard service did not reach active state.")
    for _ in range(8):
        try:
            with urlopen("http://127.0.0.1:8765/health",timeout=2) as reply:
                if reply.status==200 and reply.read(256).strip()==b'{"ok": true}':
                    return {"installed":True,"action":action,
                            "service":"active","health":"ok","exposure":"localhost_only"}
        except (OSError,TimeoutError,ValueError):
            pass
        time.sleep(0.5)
    raise RuntimeError("Dashboard service is active but HTTP health was not verified.")

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm",action="store_true",
                        help="Install or reconcile the persistent user unit.")
    parser.add_argument("--dry-run",action="store_true",
                        help="Display planned service configuration only; never write.")
    args=parser.parse_args(argv)
    try:
        if args.dry_run and args.confirm:
            parser.error("Select either dry-run or confirm.")
        if args.dry_run:
            print(unit_text(ROOT),end="")
            return 0
        if not args.confirm:
            parser.error("No change without --confirm.")
        print(install(confirmed=True))
        return 0
    except (ValueError,OSError,PermissionError,RuntimeError) as exc:
        print("Dashboard service setup refused: "+str(exc)[:150],file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
