"""Bounded multi-PC inventory, Tailscale hostname validation and dashboard auth."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import machines

class MachineInventoryTests(unittest.TestCase):
    def test_hostname_blocks_ssrf_untrusted_hosts(self):
        allowed='theraeburngroup.tail8450a3.ts.net'
        self.assertEqual(machines.hostname(allowed),allowed)
        for host in ('localhost','127.0.0.1','100.72.235.100',
                     'example.com','example.ts.net.evil.net',
                     'http://foo.tail.ts.net/','foo.tail.ts.net:443',
                     'foo.tail.ts.net/..','FOO.tail.ts.net','foo..ts.net','foo.tail.ts.net.'):
            with self.subTest(host=host),self.assertRaises(ValueError):
                machines.hostname(host)

    def test_add_requires_explicit_local_owner_confirmation(self):
        with tempfile.TemporaryDirectory() as td,patch.object(machines,'REGISTRY',Path(td)/'.local'/'machines.json'):
            with self.assertRaises(PermissionError):
                machines.add('Garuda Linux','theraeburngroup.tail8450a3.ts.net')
            data=machines.add('Garuda Linux','theraeburngroup.tail8450a3.ts.net',confirm=True)
            self.assertEqual(data['host'],'theraeburngroup.tail8450a3.ts.net')
            self.assertEqual(machines.REGISTRY.stat().st_mode & 0o077,0)
            self.assertEqual(len(machines.inventory()['machines']),1)
            with self.assertRaises(RuntimeError):
                machines.add('Second machine','theraeburngroup.tail8450a3.ts.net',confirm=True)
            with self.assertRaises(PermissionError):
                machines.remove('theraeburngroup.tail8450a3.ts.net')
            machines.remove('theraeburngroup.tail8450a3.ts.net',confirm=True)
            self.assertEqual(len(machines.read()),0)

    def test_never_execute_code_on_another_pc(self):
        with patch('machines.read',return_value=[{'name':'Machine','host':'unit.test.ts.net'}]):
            result=machines.inventory()
        self.assertFalse(result['central_remote_command_execution'])
        self.assertEqual(result['machines'][0]['reachability'],'not-checked')

    def test_https_health_check_no_redirect_following(self):
        class Connection:
            def __init__(self,*args,**kwargs):self.args=args;self.kwargs=kwargs
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def request(self,*args,**kwargs):
                self.called=args
            def getresponse(self):
                class Response:
                    status=200
                    def read(self,size):return b'{"ok":true}'
                return Response()
        with patch('machines.http.client.HTTPSConnection',Connection) as connection:
            result=machines.probe('host.tail.ts.net')
        self.assertEqual(result['reachability'],'dashboard-reachable')
        self.assertIn('Only /health was verified',result['note'])

    def test_unreachable_not_marked_as_offline_worker(self):
        with patch('machines.http.client.HTTPSConnection',side_effect=OSError('offline')):
            result=machines.probe('host.tail.ts.net')
        self.assertEqual(result['reachability'],'unreachable')

from tests.test_web_console import DashboardHTTPTests

class MachineHTTPTests(DashboardHTTPTests):
    def test_machine_inventory_requires_login(self):
        result,_,_=self.request('GET','/api/machines')
        self.assertEqual(result,401)
        with patch('machines.inventory',return_value={'machines':[]}) as check:
            result,data,_=self.request('GET','/api/machines',cookie=self.session())
        self.assertEqual(result,200)
        self.assertEqual(data['machines'],[])

    def test_machine_enrolment_requires_pc_owner(self):
        with patch('machines.add') as add:
            status,_,_=self.request('POST','/api/machines/add',{
                'name':'Other PC','host':'other.tail.ts.net','confirm':True})
            self.assertEqual(status,401)
            status,_,_=self.request('POST','/api/machines/add',{
                'name':'Other PC','host':'other.tail.ts.net'},cookie=self.session())
            self.assertEqual(status,400)
            add.assert_not_called()

    def test_probe_is_limited_to_enrolled_machine(self):
        with patch('machines.read',return_value=[]),patch('machines.probe') as probe:
            status,_,_=self.request('GET',
                '/api/machines/probe?host=rogue.tail.ts.net',cookie=self.session())
            self.assertEqual(status,403)
            probe.assert_not_called()

    def test_machine_write_rejects_cross_site_request(self):
        with patch('machines.add') as add:
            status,_,_=self.request('POST','/api/machines/add',
                {'name':'Another PC','host':'other.tail.ts.net','confirm':True},
                cookie=self.session(),origin='https://evil.example')
            self.assertEqual(status,403)
            add.assert_not_called()
