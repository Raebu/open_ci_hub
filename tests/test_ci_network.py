"""Safety regression tests for public web egress, offline default and DNS pinning."""
import os
import socket
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ci_network
import ci_bridge


class NetworkAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state=Path(self.tmp.name)

    def test_full_mode_never_accepts_pull_request_or_merge_request_network(self):
        cfg={"provider":"github","network":"full","trusted_network_confirmed":True,
             "branches":["main"]}
        manifest={"repositories":{"Raebu/network-demo":cfg}}
        for event in ("pull_request","merge_request"):
            with self.subTest(event=event),self.assertRaises(PermissionError):
                ci_bridge.enqueue("github","Raebu/network-demo","a"*40,"main",
                                  event,manifest)
    def test_ipv4_mapped_private_ip_denied(self):
        with self.assertRaises(PermissionError):
            ci_network.resolve_public("::ffff:10.1.2.3",443)

    def test_offline_default_and_full_needs_owner_confirm(self):
        with patch.object(ci_bridge,"ensure_scheduler",return_value=None):
            pass
        with patch.object(ci_bridge,"get_broker"):
            manifest={"schema":1,"repositories":{"Raebu/network-demo":{
                "provider":"github","secret_env":"CI_NETWORK_SECRET",
                "branches":["main"],"image":"python@sha256:"+"a"*64,
                "steps":[{"name":"verify","argv":["python","--version"]}]
            }}}
            path=self.state/"manifest.json";path.write_text(__import__("json").dumps(manifest))
            path.chmod(0o600)
            self.assertEqual(ci_bridge.load_manifest(path)["repositories"]["Raebu/network-demo"].get("network","offline"),"offline")
            manifest["repositories"]["Raebu/network-demo"]["network"]="full"
            path.write_text(__import__("json").dumps(manifest))
            with self.assertRaises(PermissionError):
                ci_bridge.load_manifest(path)
            manifest["repositories"]["Raebu/network-demo"]["trusted_network_confirmed"]=True
            path.write_text(__import__("json").dumps(manifest))
            self.assertEqual(ci_bridge.load_manifest(path)["repositories"]["Raebu/network-demo"]["network"],"full")
            manifest["repositories"]["Raebu/network-demo"]["network"]="host"
            path.write_text(__import__("json").dumps(manifest))
            with self.assertRaises(ValueError):
                ci_bridge.load_manifest(path)

    def test_public_resolution_pins_resolved_numeric_address(self):
        def getaddrinfo(host,port,type):
            return [(socket.AF_INET,socket.SOCK_STREAM,6,"",("8.8.8.8",port))]
        with patch("ci_network.socket.getaddrinfo",side_effect=getaddrinfo):
            result=ci_network.resolve_public("registry.npmjs.org",443)
        self.assertEqual(result[0][-1],("8.8.8.8",443))

    def test_mixed_dns_result_denied_instead_of_choosing_public(self):
        with patch("ci_network.socket.getaddrinfo",return_value=[
            (socket.AF_INET,socket.SOCK_STREAM,6,"",("8.8.8.8",443)),
            (socket.AF_INET,socket.SOCK_STREAM,6,"",("10.0.0.5",443))
        ]):
            with self.assertRaises(PermissionError):
                ci_network.resolve_public("rebind.example",443)

    def test_private_ip_literals_and_metadata_denied(self):
        targets=["127.0.0.1","10.0.1.1","192.168.1.73","172.16.0.12",
                 "169.254.169.254","100.64.0.1","0.0.0.0","224.0.0.1",
                 "255.255.255.255","::1","fc00::1","fe80::2"]
        for host in targets:
            with self.subTest(host=host):
                with self.assertRaises(PermissionError):
                    ci_network.resolve_public(host,443)

    def test_public_web_ports_only(self):
        with self.assertRaises(PermissionError):
            ci_network.resolve_public("8.8.8.8",22)
        with self.assertRaises(PermissionError):
            ci_network.authority("169.254.169.254:444")
        with self.assertRaises(PermissionError):
            ci_network.resolve_public("something.local",443)

    def test_private_proxy_socket_denies_local_requests(self):
        with ci_network.public_web_proxy(self.state,"a"*32) as directory:
            path=directory/"proxy.sock"
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
            for target in ["127.0.0.1:443","169.254.169.254:80","192.168.1.73:443","10.0.0.1:443"]:
                client=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
                client.settimeout(3)
                client.connect(str(path))
                client.sendall(("CONNECT "+target+" HTTP/1.1\r\nHost: "+target+"\r\n\r\n").encode())
                self.assertIn(b"403 Forbidden",client.recv(256))
                client.close()
        self.assertFalse(directory.exists())

    def test_network_options_do_not_remove_network_none(self):
        opts=ci_network.docker_network_options(Path("/sandbox/static"),Path("/sandbox/socket"))
        self.assertIn("--entrypoint",opts)
        self.assertNotIn("--network",opts)
        self.assertTrue(all("host.docker.internal" not in v for v in opts))
        self.assertIn("NO_PROXY=",opts)

    def test_static_compiler_source_can_be_built_nonroot(self):
        src=Path(ci_network.__file__).with_name("ci_proxy_bridge.c")
        binary=ci_network.ensure_static_bridge(src,self.state)
        self.assertTrue(binary.is_file())
        self.assertFalse(binary.stat().st_mode & 0o077)
        self.assertEqual(ci_network.ensure_static_bridge(src,self.state),binary)


if __name__=="__main__":
    unittest.main()
