#!/usr/bin/env python3
"""Loopback-free, per-job public-web egress over a private Unix socket.

Docker job retains --network=none; no host, LAN, metadata or private routes.
Only outbound HTTP proxy/CONNECT on ports 80/443, to DNS-pinned global IPs.
This is not an unrestricted TCP/UDP internet gateway.
"""
from __future__ import annotations

from contextlib import contextmanager
import ipaddress
import os
from pathlib import Path
import re
import time
import selectors
import shutil
import socket
import socketserver
import threading
from urllib.parse import urlsplit

SOCKET_NAME = "proxy.sock"
MAX_HEADER = 16_384
MAX_CLIENTS = 24
VALID_MODES = frozenset(("offline", "full"))
APPROVED_PORTS = (80, 443)
HTTP_METHODS = {"GET","HEAD","POST","PUT","PATCH","DELETE","OPTIONS"}
DOMAIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9])?$")


def resolve_public(host: str, port: int):
    if not isinstance(host, str) or len(host) > 253 or not host:
        raise PermissionError("Invalid network destination")
    if type(port) is not int or port not in APPROVED_PORTS:
        raise PermissionError("Only public web ports 80/443 allowed")
    if any(c in host for c in "/\\@#?%") or not DOMAIN.fullmatch(host):
        # IPv6 literals use ':'; private literals are refused regardless.
        try: ipaddress.ip_address(host)
        except ValueError: raise PermissionError("Invalid destination name") from None
    if host.lower().endswith((".local", ".internal", ".localhost")):
        raise PermissionError("Local and internal DNS names blocked")
    try:
        answers = socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        raise PermissionError("Destination did not resolve") from None
    if not answers:
        raise PermissionError("Destination did not resolve")
    vetted=[]
    for family,socktype,proto,_,sockaddr in answers:
        ip=ipaddress.ip_address(sockaddr[0])
        if getattr(ip,"ipv4_mapped",None):
            ip=ip.ipv4_mapped
        # Reject the entire name if ANY answer is private, even if mixed
        # with global addresses. No direct DNS requests from CI container.
        if not ip.is_global or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            raise PermissionError("Non-public/private/metadata network denied")
        vetted.append((family,socktype,proto,sockaddr))
    return vetted


def approved_connection(host,port):
    answers=resolve_public(host,port)
    for family,kind,proto,address in answers:
        conn=socket.socket(family,kind,proto)
        try:
            conn.settimeout(12)
            conn.connect(address)   # DNS-pinned numeric sockaddr, not hostname
            conn.settimeout(None)
            return conn
        except OSError:
            conn.close()
    raise OSError("Approved public web destination unreachable")


def authority(value: str):
    if not isinstance(value,str) or len(value)>270:
        raise PermissionError("Invalid proxy authority")
    parsed=urlsplit("//"+value)
    if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise PermissionError("Invalid proxy authority")
    host=parsed.hostname
    try: port=parsed.port
    except ValueError: raise PermissionError("Invalid proxy port") from None
    if not host or port not in APPROVED_PORTS:
        raise PermissionError("Only public web ports 80/443 allowed")
    return host,port


def relay(a:socket.socket,b:socket.socket):
    selector=selectors.DefaultSelector()
    try:
        a.setblocking(False);b.setblocking(False)
        selector.register(a,selectors.EVENT_READ,b)
        selector.register(b,selectors.EVENT_READ,a)
        last_active=time.monotonic()
        while selector.get_map():
            if time.monotonic()-last_active>180:
                break
            for key,_ in selector.select(timeout=1):
                origin=key.fileobj; destination=key.data
                try: data=origin.recv(65536)
                except BlockingIOError: continue
                except ConnectionResetError: data=b""
                if not data:
                    try: selector.unregister(origin)
                    except (KeyError,ValueError): pass
                    try: destination.shutdown(socket.SHUT_WR)
                    except OSError: pass
                    continue
                last_active=time.monotonic()
                try:
                    destination.settimeout(20)
                    destination.sendall(data)
                    destination.setblocking(False)
                except OSError:
                    try: selector.unregister(origin)
                    except (KeyError,ValueError): pass
    finally:
        selector.close()


class RequestHandler(socketserver.BaseRequestHandler):
    def handle(self):
        if not self.server.slots.acquire(blocking=False):
            self.request.sendall(b"HTTP/1.1 503 Busy\r\nContent-Length: 0\r\n\r\n")
            return
        try: self._handle()
        finally: self.server.slots.release()

    def _handle(self):
        self.request.settimeout(15)
        header=b""
        while b"\r\n\r\n" not in header:
            data=self.request.recv(min(4096,MAX_HEADER-len(header)+1))
            if not data or len(header)+len(data)>MAX_HEADER:
                return self._deny()
            header+=data
        head,remaining=header.split(b"\r\n\r\n",1)
        try:
            first,*lines=head.decode("latin-1").split("\r\n")
            method,url,proto=first.split(" ")
            if proto not in ("HTTP/1.0","HTTP/1.1"):
                raise PermissionError()
            if method=="CONNECT":
                host,port=authority(url)
                upstream=approved_connection(host,port)
                self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            elif method in HTTP_METHODS:
                parsed=urlsplit(url)
                if (parsed.scheme!="http" or parsed.username or parsed.password or
                        parsed.fragment or parsed.port not in (None,80) or not parsed.hostname):
                    raise PermissionError()
                upstream=approved_connection(parsed.hostname,80)
                path=parsed.path or "/"
                if parsed.query:path+="?"+parsed.query
                clean=[f"{method} {path} {proto}"]
                for line in lines:
                    if ":" not in line:raise PermissionError()
                    key=line.split(":",1)[0].strip().lower()
                    if key in ("proxy-authorization","proxy-connection"):
                        continue
                    clean.append(line)
                upstream.sendall(("\r\n".join(clean)+"\r\n\r\n").encode("latin-1"))
            else: raise PermissionError()
            with upstream:
                if remaining:upstream.sendall(remaining)
                relay(self.request,upstream)
        except (PermissionError,ValueError,UnicodeError):
            self._deny()
        except OSError:
            self._bad_gateway()

    def _deny(self):
        try:self.request.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        except OSError: pass

    def _bad_gateway(self):
        try:self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        except OSError: pass


class ProxyServer(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):
    daemon_threads=True
    allow_reuse_address=False
    def __init__(self,path):
        self.slots=threading.BoundedSemaphore(MAX_CLIENTS)
        super().__init__(str(path),RequestHandler)


@contextmanager
def public_web_proxy(state:Path,job_id:str):
    if not re.fullmatch(r"[a-f0-9]{32}",job_id):
        raise ValueError("Invalid CI job ID")
    # AF_UNIX sun_path is limited to 107 bytes on Linux. Project paths may
    # be long; the per-user runtime dir is short, private and ephemeral.
    runtime=Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    if (runtime.is_symlink() or not runtime.is_dir() or
            runtime.stat().st_uid != os.getuid() or runtime.stat().st_mode & 0o077):
        raise PermissionError("Private owner runtime directory unavailable")
    parent=runtime/"oci-egress"
    if parent.is_symlink():
        raise PermissionError("Symlinked egress root refused")
    parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o077:
        raise PermissionError("Egress directory must be private")
    os.chmod(parent,0o700)
    work=parent/job_id
    if work.exists() or work.is_symlink():
        raise RuntimeError("Egress socket directory already exists; fail closed")
    work.mkdir(mode=0o700)
    server=None;thread=None
    try:
        server=ProxyServer(work/SOCKET_NAME)
        os.chmod(work/SOCKET_NAME,0o600)
        thread=threading.Thread(target=server.serve_forever,daemon=True,name="ci-web-egress")
        thread.start()
        yield work
    finally:
        if server:
            server.shutdown()
            server.server_close()
        if thread:thread.join(timeout=3)
        shutil.rmtree(work)


def ensure_static_bridge(source: Path, state: Path):
    """Build a static trusted relay in the private runner directory."""
    import hashlib
    import subprocess
    source=Path(source)
    if source.is_symlink() or not source.is_file():
        raise PermissionError("Missing/unsafe trusted bridge source")
    checksum=hashlib.sha256(source.read_bytes()).hexdigest()[:20]
    target=Path(state)/"bin"
    if target.is_symlink():
        raise PermissionError("Symlinked executable directory refused")
    target.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(target,0o700)
    binary=target/("ci-proxy-bridge-"+checksum)
    if binary.is_symlink():
        raise PermissionError("Symlinked connector refused")
    if not binary.exists():
        temporary=target/(".compile-"+checksum+"-"+str(os.getpid()))
        try:
            completed=subprocess.run(["gcc","-static","-std=c11","-O2",
                       "-Wall","-Wextra","-Werror",str(source),"-o",str(temporary)],
                       check=False,timeout=30,stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
            if completed.returncode!=0:
                raise RuntimeError("CI network connector compilation failed")
            os.chmod(temporary,0o500)
            os.replace(temporary,binary)
        finally:
            if temporary.exists():temporary.unlink()
    if not binary.is_file() or binary.stat().st_mode & 0o077:
        raise PermissionError("CI network connector permissions invalid")
    return binary


def docker_network_options(binary:Path, socket_dir:Path):
    """Options for a --network none container; no direct host network."""
    if not binary or not socket_dir:
        raise ValueError("Public proxy socket and executable required")
    return [
       "--mount",f"type=bind,src={binary},dst=/usr/local/bin/ci-proxy-bridge,readonly",
       "--mount",f"type=bind,src={socket_dir},dst=/run/ci-egress,readonly",
       "--entrypoint","/usr/local/bin/ci-proxy-bridge",
       "-e","HTTPS_PROXY=http://127.0.0.1:3128",
       "-e","HTTP_PROXY=http://127.0.0.1:3128",
       "-e","https_proxy=http://127.0.0.1:3128",
       "-e","http_proxy=http://127.0.0.1:3128",
       "-e","NO_PROXY=","-e","no_proxy="
    ]


def network_overview():
    """Safe dashboard view. No secret names, private config paths or credentials."""
    import ci_bridge
    try:
        manifest=ci_bridge.load_manifest()
    except (OSError,ValueError,PermissionError):
        return {"configured":False,"projects":[],"note":"Private CI manifest missing or invalid."}
    projects=[]
    for name,cfg in sorted(manifest["repositories"].items()):
        mode=cfg.get("network","offline")
        projects.append({"repo":name,"provider":cfg["provider"],
                         "mode":mode,"trusted":cfg.get("trusted_network_confirmed") is True,
                         "branches":cfg["branches"],"requests_via_proxy":mode=="full"})
    return {"configured":True,"projects":projects,
            "network_enforcement":"network-none + public-web Unix proxy; private/LAN/metadata blocked",
            "direct_internet_socket_access":False,
            "note":"Full means public HTTP/HTTPS only, TCP ports 80/443. Other TCP/UDP ports and direct LAN access are not permitted."}
