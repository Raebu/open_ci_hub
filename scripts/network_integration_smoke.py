#!/usr/bin/env python3
"""Bounded, non-production Docker proof for the CI public-Web proxy.

It never alters live CI jobs or host firewall settings.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from ci_network import public_web_proxy,ensure_static_bridge,docker_network_options

ROOT=Path(__file__).resolve().parent.parent
STATE=ROOT/".local"/"ci-bridge"
IMAGE="python:3.12-slim"
TEST_PROGRAM=r'''
import json, os, socket, urllib.request, urllib.error
checks={}
try:
  with urllib.request.urlopen("https://registry.npmjs.org/-/ping",timeout=13) as r:
    checks["public_https"]=r.status==200
except Exception as e:
  checks["public_https"]=False
for name,target in [
 ("loopback","127.0.0.1:443"),
 ("lan","192.168.1.73:443"),
 ("metadata","169.254.169.254:80"),
 ("rfc1918","10.1.1.1:443")]:
  try:
    with socket.create_connection(("127.0.0.1",3128),timeout=5) as conn:
      conn.sendall(("CONNECT "+target+" HTTP/1.1\\r\\nHost: "+target+"\\r\\n\\r\\n").replace("\\r","\r").replace("\\n","\n").encode("ascii"))
      result=conn.recv(200)
      checks[name+"_blocked"]=b"403 Forbidden" in result
  except Exception:
    checks[name+"_blocked"]=False
try:
  c=socket.create_connection(("1.1.1.1",443),timeout=3)
  c.close()
  checks["direct_public_blocked"]=False
except OSError:
  checks["direct_public_blocked"]=True
print("OPEN_CI_NETWORK_SMOKE="+json.dumps(checks,sort_keys=True))
sys_ok=all(checks.values())
raise SystemExit(0 if sys_ok else 12)
'''
def main():
    uid=os.getuid();gid=os.getgid()
    helper=ensure_static_bridge(ROOT/"ci_proxy_bridge.c",STATE)
    with public_web_proxy(STATE,"b"*32) as sockdir:
        args=["docker","run","--rm","--network","none","--read-only",
            "--cap-drop","ALL","--security-opt","no-new-privileges",
            "--pids-limit","48","--memory","256m","--memory-swap","256m",
            "--cpus","0.5","--tmpfs","/tmp:rw,nosuid,nodev,size=32m",
            "--user",f"{uid}:{gid}","-e","HOME=/tmp"]
        args+=docker_network_options(helper,sockdir)
        args+=[IMAGE,"python","-c",TEST_PROGRAM]
        result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                             stdin=subprocess.DEVNULL,text=True,timeout=60,check=False)
    print(result.stdout[-3000:].strip())
    if result.returncode:
        print("Network smoke failed with exit code",result.returncode,file=sys.stderr)
        print(result.stderr[-600:],file=sys.stderr)
    return result.returncode

if __name__=="__main__":
    sys.exit(main())
