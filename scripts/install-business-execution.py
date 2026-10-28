#!/usr/bin/env python3
"""Install exactly one non-root Raeburn commercial shadow scanner user timer.

A clean, pushed main is required. Existing units may only be reused if exact.
No ChatGPT tasks, credentials, inboxes, cloud projects or repository branches
are altered. The worker can only read canonical/local sources.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
NAME = "open-ci-hub-business-execution"


def unit_text(root):
    return (
        "[Unit]\n"
        "Description=Raeburn business source reconciliation (parallel shadow migration)\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"WorkingDirectory={root}\n"
        f"ExecStart=/usr/bin/python3 {root}/business_execution.py run\n"
        "TimeoutStartSec=145\n"
        "CPUQuota=25%\n"
        "MemoryMax=256M\n"
        "Nice=17\n"
        "NoNewPrivileges=true\n"
        "PrivateTmp=true\n"
        "UMask=0077\n"
    )


def timer_text():
    return (
        "[Unit]\n"
        "Description=Review canonical Raeburn business sources twice hourly\n\n"
        "[Timer]\n"
        "OnCalendar=*-*-* *:12,42:00\n"
        "Persistent=true\n"
        "AccuracySec=60s\n"
        f"Unit={NAME}.service\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def checked(*args, timeout=15):
    result=subprocess.run(args,capture_output=True,text=True,
                          stdin=subprocess.DEVNULL,timeout=timeout,check=False)
    if result.returncode:
        raise RuntimeError("Precondition or systemd step failed: "+args[0])
    return result.stdout.strip()


def install(root=ROOT,home=None,*,confirm=False):
    if confirm is not True:
        raise PermissionError("Explicit owner approval required.")
    root=Path(root).resolve(strict=True)
    if root.name!="open-ci-hub" or os.geteuid()==0:
        raise PermissionError("Requires existing non-root Open CI Hub checkout.")
    if checked("git","-C",str(root),"branch","--show-current")!="main":
        raise RuntimeError("Only reviewed main branch may install.")
    if checked("git","-C",str(root),"status","--porcelain"):
        raise RuntimeError("Commit and verify source before installation.")
    if checked("git","-C",str(root),"rev-parse","HEAD")!=checked(
            "git","-C",str(root),"rev-parse","origin/main"):
        raise RuntimeError("Publish reviewed main before activation.")
    directory=(Path.home() if home is None else Path(home))/".config"/"systemd"/"user"
    if directory.is_symlink():
        raise PermissionError("Symlinked unit directory refused.")
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    plans=((directory/f"{NAME}.service",unit_text(root)),
           (directory/f"{NAME}.timer",timer_text()))
    # Validate both before touching either; preserve any conflicting unit.
    for path,source in plans:
        if path.is_symlink() or (path.exists() and path.read_text()!=source):
            raise RuntimeError("Pre-existing service/timer conflict; no replacement.")
    for path,source in plans:
        if not path.exists():
            fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,"w",encoding="utf8") as out:
                out.write(source)
    checked("systemctl","--user","daemon-reload")
    checked("systemctl","--user","enable","--now",f"{NAME}.timer")
    checked("systemctl","--user","start",f"{NAME}.service",timeout=160)
    state=checked("systemctl","--user","show",f"{NAME}.service",
                  "-p","Result","-p","ExecMainStatus")
    if "Result=success" not in state or "ExecMainStatus=0" not in state:
        raise RuntimeError("Initial source run did not complete successfully.")
    return {"installed":True,"unit":NAME,"mode":"parallel_shadow",
            "existing_chatgpt_tasks":"preserved",
            "timer":checked("systemctl","--user","is-enabled",f"{NAME}.timer")}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm",action="store_true")
    parser.add_argument("--dry-run",action="store_true")
    args=parser.parse_args(argv)
    if args.dry_run:
        print(unit_text(ROOT))
        print(timer_text())
        return 0
    try:
        print(install(confirm=args.confirm))
        return 0
    except (PermissionError,RuntimeError,OSError,subprocess.TimeoutExpired) as exc:
        print("Business timer installation refused: "+str(exc)[:160],file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
