#!/usr/bin/env python3
"""Restricted cross-platform edge worker for Windows ARM64 and Linux.

Uses the EXISTING private, token-scoped Open CI Hub application job API.
No arbitrary command, PR scripts, file reads or external browser access.
"""
from __future__ import annotations
import argparse
import ast
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ALLOWED=frozenset(("python.syntax","json.inspect","hash.benchmark"))
MAX_INPUT=2048
MAX_RAM_MB=256

def task(task_name, payload):
    if task_name not in ALLOWED or not isinstance(payload,dict):
        raise ValueError("Task or payload not approved.")
    if task_name=="python.syntax":
        source=payload.get("source")
        if not isinstance(source,str) or len(source.encode("utf8"))>1500:
            raise ValueError("Invalid source size.")
        try:
            nodes=sum(1 for _ in ast.walk(ast.parse(source)))
            return {"valid":True,"nodes":nodes,"sha256":hashlib.sha256(source.encode()).hexdigest()}
        except SyntaxError as e:
            return {"valid":False,"line":e.lineno,"sha256":hashlib.sha256(source.encode()).hexdigest()}
    if task_name=="json.inspect":
        source=payload.get("text")
        if not isinstance(source,str) or len(source.encode())>1500:
            raise ValueError("Invalid JSON size.")
        try:
            data=json.loads(source)
            encoded=json.dumps(data,sort_keys=True,separators=(",",":"),allow_nan=False,ensure_ascii=True)
            return {"valid":True,"kind":type(data).__name__,
                    "sha256":hashlib.sha256(encoded.encode()).hexdigest()}
        except (ValueError,OverflowError):
            return {"valid":False}
    value=payload.get("text"); iterations=payload.get("iterations")
    if (not isinstance(value,str) or len(value.encode())>1500 or
        type(iterations) is not int or not 1000<=iterations<=50000):
        raise ValueError("Invalid benchmark.")
    return {"sha256":hashlib.pbkdf2_hmac("sha256",value.encode(),
             b"open-ci-edge-benchmark-v1",iterations).hex(),"iterations":iterations}

def available_ram_mb():
    if sys.platform=="win32":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_=[("dwLength",ctypes.c_ulong),("dwMemoryLoad",ctypes.c_ulong),
                ("ullTotalPhys",ctypes.c_ulonglong),("ullAvailPhys",ctypes.c_ulonglong),
                ("ullTotalPageFile",ctypes.c_ulonglong),("ullAvailPageFile",ctypes.c_ulonglong),
                ("ullTotalVirtual",ctypes.c_ulonglong),("ullAvailVirtual",ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual",ctypes.c_ulonglong)]
        state=MEMORYSTATUSEX();state.dwLength=ctypes.sizeof(state)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
            return None
        return state.ullAvailPhys//1048576
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1])//1024
    except (OSError,ValueError):
        pass
    return None

def windows_ac_connected():
    if sys.platform!="win32":
        return True
    class STATUS(ctypes.Structure):
        _fields_=[("ACLineStatus",ctypes.c_byte),("BatteryFlag",ctypes.c_byte),
                 ("BatteryLifePercent",ctypes.c_byte),("SystemStatusFlag",ctypes.c_byte),
                 ("BatteryLifeTime",ctypes.c_ulong),("BatteryFullLifeTime",ctypes.c_ulong)]
    state=STATUS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(state)):
        return False
    return state.ACLineStatus==1

def samsung_book_go_memory_profile():
    """Explicit opt-in, exact hardware, never lower general Linux/Windows limits."""
    if os.environ.get("OCI_EDGE_MEMORY_PROFILE")!="samsung-linux-edge":
        return False
    if (platform.system()!="Linux" or platform.machine().lower() not in
            ("aarch64","arm64") or platform.node()!="samsung-book-go"):
        return False
    try:
        model=Path("/proc/device-tree/model").read_bytes().rstrip(b"\x00").decode("utf8")
        return model=="Samsung Galaxy Book Go"
    except (OSError,UnicodeError):
        return False


def minimum_available_ram_mb(system):
    return 896 if system=="Linux" and samsung_book_go_memory_profile() else 1536


def ready(*, ram=available_ram_mb, powered=windows_ac_connected, system=None):
    system=platform.system() if system is None else system
    if system not in ("Windows","Linux"):
        return False,"unsupported_operating_system"
    remaining=ram()
    if type(remaining) is not int or remaining<minimum_available_ram_mb(system):
        return False,"foreground_memory_reserved"
    if system=="Windows" and not powered():
        return False,"not_connected_to_power"
    return True,"ready"

def _child_main(kind):
    try:
        blob=sys.stdin.buffer.read(MAX_INPUT+1)
        if len(blob)>MAX_INPUT:
            raise ValueError("Payload exceeds cap.")
        result=task(kind,json.loads(blob))
        print(json.dumps({"success":True,"result":result},separators=(",",":"),allow_nan=False))
    except (ValueError,TypeError,OverflowError,RecursionError):
        print('{"success":false,"result":{"error":"invalid_payload"}}')

def execute(kind,payload,*,spawn=subprocess.run):
    if kind not in ALLOWED or not isinstance(payload,dict):
        return False,{"error":"unsupported_task"}
    try:
        blob=json.dumps(payload,separators=(",",":"),allow_nan=False).encode()
        if len(blob)>MAX_INPUT:
            return False,{"error":"payload_too_large"}
        env={"PATH":os.environ.get("PATH",""),"HOME":str(Path.home()),"PYTHONNOUSERSITE":"1"}
        if sys.platform=="win32":
            env.update({"SYSTEMROOT":os.environ.get("SYSTEMROOT","C:\\Windows"),
                        "TEMP":os.environ.get("TEMP","C:\\Windows\\Temp"),
                        "TMP":os.environ.get("TMP","C:\\Windows\\Temp")})
        else:
            env["LANG"]="C.UTF-8"
        options={}
        if sys.platform=="win32":
            options["creationflags"]=getattr(subprocess,"BELOW_NORMAL_PRIORITY_CLASS",0) | getattr(subprocess,"CREATE_NO_WINDOW",0)
        else:
            import resource
            def cap():
                os.nice(15)
                resource.setrlimit(resource.RLIMIT_AS,(MAX_RAM_MB*1048576,MAX_RAM_MB*1048576))
                resource.setrlimit(resource.RLIMIT_CPU,(10,11))
                resource.setrlimit(resource.RLIMIT_CORE,(0,0))
            options["preexec_fn"]=cap
        result=spawn([sys.executable,"-I",str(Path(__file__).resolve()),"_child",kind],
                   input=blob,capture_output=True,timeout=15,env=env,check=False,**options)
        if result.returncode or len(result.stdout)>2048:
            return False,{"error":"worker_execution_failed"}
        response=json.loads(result.stdout)
        if type(response.get("success")) is not bool or not isinstance(response.get("result"),dict):
            raise ValueError("Invalid subprocess response.")
        return response["success"],response["result"]
    except (OSError,ValueError,TypeError,subprocess.TimeoutExpired,PermissionError):
        return False,{"error":"worker_execution_failed"}

def once(client,*,admit=ready):
    ok,reason=admit()
    if not ok:
        return {"status":"deferred","reason":reason}
    sysname=platform.system()
    machine=platform.machine().lower()
    arch=("arm64" if machine in ("aarch64","arm64") else
          "x86_64" if machine in ("amd64","x86_64") else "unsupported")
    available=available_ram_mb()
    # Device identity and resource telemetry are bound to the owner's
    # separately issued worker token. The worker cannot enrol itself.
    if arch=="unsupported" or type(available) is not int:
        return {"status":"deferred","reason":"unsupported_device_telemetry"}
    presence=client.availability(sysname,arch,available,windows_ac_connected())
    if not isinstance(presence,dict) or presence.get("enrolled") is not True:
        return {"status":"deferred","reason":"owner_enrolment_required"}
    if presence.get("online") is not True:
        return {"status":"deferred","reason":"remote_capacity_gate"}
    claimed=client.claim()
    if not isinstance(claimed,dict) or ("job" not in claimed):
        raise ValueError("Invalid broker claim.")
    job=claimed["job"]
    if job is None:
        return {"status":"idle"}
    if not isinstance(job,dict) or any(not isinstance(job.get(x),str) for x in ("job_id","lease_token","task")):
        raise ValueError("Invalid worker lease.")
    resources=job.get("resources")
    eligible,reason=admit()
    if (not isinstance(resources,dict) or
        type(resources.get("ram_mb")) is not int or
        not 64<=resources["ram_mb"]<=MAX_RAM_MB or
        resources.get("vram_mb")!=0 or not eligible):
        success,result=False,{"error":"worker_resource_gate","reason":reason if not eligible else "unsupported_budget"}
    else:
        success,result=execute(job["task"],job.get("payload"))
    response=client.complete(job["job_id"],job["lease_token"],success,result)
    return {"status":"acknowledged","job_id":job["job_id"],
            "hub_status":response.get("status"),"success":success}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test",action="store_true")
    parser.add_argument("--once",action="store_true")
    parser.add_argument("--interval",type=int,default=60)
    parser.add_argument("internal",nargs="?")
    parser.add_argument("kind",nargs="?")
    opts=parser.parse_args(argv)
    if opts.internal=="_child":
        _child_main(opts.kind)
        return 0
    if opts.internal or not 30<=opts.interval<=600:
        parser.error("Invalid execution mode.")
    if opts.self_test:
        print(json.dumps({"device":platform.system(),"architecture":platform.machine(),
              "admission":ready(),"tasks":sorted(ALLOWED),
              "tests":[task("json.inspect",{"text":'{"a":2,"b":1}'})["valid"],
                       task("python.syntax",{"source":"x=1"})["valid"]]}))
        return 0
    from application_client import ApplicationClient
    try:
        client=ApplicationClient(os.getenv("OCI_EDGE_URL",""),
                                 os.getenv("OCI_EDGE_WORKER_TOKEN",""))
    except ValueError:
        print("Invalid private Hub URL or worker token.",file=sys.stderr)
        return 2
    while True:
        try:
            print(json.dumps(once(client),separators=(",",":")),flush=True)
        except (ValueError,OSError,RuntimeError) as error:
            print('{"status":"hub_unavailable_or_invalid_receipt"}',flush=True)
        if opts.once:
            break
        try:
            time.sleep(opts.interval)
        except KeyboardInterrupt:
            break
    return 0

if __name__=="__main__":
    raise SystemExit(main())
