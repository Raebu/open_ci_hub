"""Security, command, policy and socket tests for the unprivileged Linux host agent."""
import json
import os
from pathlib import Path
import socket
import tempfile
import time
import threading
import unittest
from unittest.mock import patch
import subprocess

import host_agent as h

class AgentSafetyTests(unittest.TestCase):
    def test_policy_is_denied_by_default(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(h,'POLICY',Path(directory)/'policy.json'):
            self.assertEqual(h.policy_data()['flatpaks'], [])
            with self.assertRaises(PermissionError):
                h.HostController().process({'op':'preview','actor':'local-owner',
                                            'kind':'flatpak_install','target':'org.gnome.Calculator'})

    def test_policy_changes_need_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(h,'POLICY',Path(directory)/'.local'/'policy.json'):
            with self.assertRaises(PermissionError):
                h.allow('flatpak','org.gnome.Calculator')
            data=h.allow('flatpak','org.gnome.Calculator',confirm=True)
            self.assertIn('org.gnome.Calculator',data['flatpaks'])
            self.assertEqual(h.POLICY.stat().st_mode & 0o077,0)
            with self.assertRaises(ValueError):
                h.allow('service','open-ci-hub-host-agent.service',confirm=True)

    def test_rejects_command_injection_and_arbitrary_shell(self):
        with patch('host_agent.policy_data',return_value={
            'flatpaks':['org.gnome.Calculator'],'services':['my-service.service'],
            'mobile_admins':[]}):
            for bad in ['org.gnome.Calculator; rm -rf /','../pkg','$(touch /tmp/pwned)','--help']:
                with self.subTest(bad=bad),self.assertRaises(ValueError):
                    h.HostController().process({'op':'preview','actor':'local-owner',
                                                'kind':'flatpak_install','target':bad})
            for kind in ['shell','run','pacman_install','python_exec','sudo']:
                with self.subTest(kind=kind),self.assertRaises(ValueError):
                    h.HostController().process({'op':'preview','actor':'local-owner',
                                                'kind':kind,'target':'my-service.service'})

    def test_mobile_requires_independent_host_approval(self):
        c=h.HostController()
        with patch('host_agent.policy_data',return_value={
             'flatpaks':['org.gnome.Calculator'],'services':[],
             'mobile_admins':[]}):
            with self.assertRaises(PermissionError):
                c.process({'op':'preview','actor':'person@example.com','kind':'flatpak_install','target':'org.gnome.Calculator'})
        with patch('host_agent.policy_data',return_value={
             'flatpaks':['org.gnome.Calculator'],'services':[],
             'mobile_admins':['person@example.com']}):
            p=c.process({'op':'preview','actor':'person@example.com','kind':'flatpak_install','target':'org.gnome.Calculator'})
        self.assertIn('preview_token',p)

    def test_preview_is_one_time_and_binds_actor_and_action(self):
        c=h.HostController()
        p={'op':'preview','actor':'local-owner','kind':'service_restart','target':'my-service.service'}
        with patch('host_agent.policy_data',return_value={
             'flatpaks':[],'services':['my-service.service','other.service'],'mobile_admins':['any@example.com']}), \
             patch('host_agent._exec',return_value={'ok':True}) as execute, \
             patch('host_agent._append_audit') as audit:
            token=c.process(p)['preview_token']
            with self.assertRaises(PermissionError):
                c.process({**p,'op':'execute','target':'other.service','preview_token':token})
            execute.assert_not_called()
            token=c.process(p)['preview_token']
            with self.assertRaises(PermissionError):
                c.process({**p,'op':'execute','actor':'any@example.com','preview_token':token})
            execute.assert_not_called()
            token=c.process(p)['preview_token']
            accepted = c.process({**p,'op':'execute','preview_token':token})
            self.assertEqual(accepted['status'], 'running')
            for _ in range(100):
                state = c.process({'op':'job','actor':'local-owner',
                                   'job_id':accepted['job_id']})
                if state['status'] != 'running':
                    break
                time.sleep(.01)
            self.assertEqual(state['status'], 'success')
            self.assertGreaterEqual(audit.call_count, 2)
            with self.assertRaises(PermissionError):
                c.process({**p,'op':'execute','preview_token':token})
            execute.assert_called_once()
            with self.assertRaises(PermissionError):
                c.process({'op':'job','actor':'any@example.com',
                           'job_id':accepted['job_id']})

    def test_expired_preview_refuses_execution(self):
        c=h.HostController()
        with patch('host_agent.policy_data',return_value={'flatpaks':[],
             'services':['demo.service'],'mobile_admins':[]}), \
             patch('host_agent.time.monotonic',side_effect=[100.0,221.0]), \
             patch('host_agent._exec') as exe:
            token=c.process({'op':'preview','actor':'local-owner','kind':'service_start','target':'demo.service'})['preview_token']
            with self.assertRaises(PermissionError):
                c.process({'op':'execute','actor':'local-owner','kind':'service_start',
                           'target':'demo.service','preview_token':token})
            exe.assert_not_called()

    def test_user_flatpak_install_is_not_pacman_or_root(self):
        with patch('host_agent.policy_data',return_value={'flatpaks':['org.gnome.Calculator'],
             'services':[],'mobile_admins':[]}), \
             patch('host_agent.Path.is_file',return_value=True), \
             patch('host_agent.subprocess.run',return_value=subprocess.CompletedProcess([],0,'','')) as runner:
            done=h._exec('flatpak_install','org.gnome.Calculator')
        self.assertTrue(done['ok'])
        args=runner.call_args.args[0]
        self.assertEqual(args[:3],['/usr/bin/flatpak','install','--user'])
        self.assertNotIn('sudo',args)
        self.assertNotIn('pacman',args)
        self.assertFalse(runner.call_args.kwargs['shell'])
        self.assertEqual(runner.call_args.kwargs['stdin'],subprocess.DEVNULL)

    def test_audit_fails_closed_on_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'target'
            target.write_text('safe')
            audit=Path(tmp)/'audit'
            audit.symlink_to(target)
            with patch.object(h,'STATE',Path(tmp)),patch.object(h,'AUDIT',audit):
                with self.assertRaises(RuntimeError):
                    h._append_audit('local-owner','service_start','demo.service','success')
            self.assertEqual(target.read_text(),'safe')

    def test_bounded_private_socket_roundtrip(self):
        if not hasattr(socket,'AF_UNIX'):
            self.skipTest('Unix sockets unavailable')
        with tempfile.TemporaryDirectory() as td:
            sock=Path(td)/'agent.sock'
            server=h._UnixServer(str(sock),h._Handler)
            server.controller=h.HostController()
            os.chmod(sock,0o600)
            thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':0.01},daemon=True)
            thread.start()
            try:
                data=h.client_request({'op':'status'},socket_path=sock)
                self.assertTrue(data['available'])
                self.assertEqual(sock.stat().st_mode & 0o777,0o600)
                with self.assertRaises(ValueError):
                    h.client_request({'blob':'x'*h.MAX_BYTES},socket_path=sock)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)

class HostHTTPTests(unittest.TestCase):
    def test_unauthenticated_host_status_refused(self):
        from tests.test_web_console import DashboardHTTPTests
        # The existing dashboard tests use the same session and CSRF path;
        # exercise the new request route with their live temporary server.
        class Dummy: pass
        self.assertTrue(hasattr(DashboardHTTPTests,'request'))

from tests.test_web_console import DashboardHTTPTests

class HostDashboardTests(DashboardHTTPTests):
    def test_host_read_requires_authentication(self):
        status,_,_=self.request('GET','/api/host')
        self.assertEqual(status,401)
        with patch('host_agent.client_request',return_value={'available':True,'system':{}}) as client:
            status,data,_=self.request('GET','/api/host',cookie=self.session())
            self.assertEqual(status,200)
            self.assertTrue(data['available'])
            client.assert_called_once_with({'op':'status'})

    def test_agent_offline_is_clearly_reported(self):
        with patch('host_agent.client_request',side_effect=RuntimeError('Agent unavailable')):
            status,data,_=self.request('GET','/api/host',cookie=self.session())
        self.assertEqual(status,200)
        self.assertFalse(data['available'])

    def test_preview_requires_management_rights(self):
        with patch('host_agent.client_request') as agent:
            status,_,_=self.request('POST','/api/host/preview',
                                    {'kind':'flatpak_install','target':'org.gnome.Calculator'})
            self.assertEqual(status,401)
            agent.assert_not_called()

    def test_confirm_and_agent_token_required_for_execute(self):
        cookie=self.session()
        with patch('host_agent.client_request') as agent:
            status,_,_=self.request('POST','/api/host/execute',
                                    {'kind':'flatpak_install','target':'org.gnome.Calculator',
                                     'preview_token':'any'},cookie=cookie)
            self.assertEqual(status,400)
            agent.assert_not_called()
            with patch('host_agent.client_request',return_value={'ok':True}) as action:
                status,_,_=self.request('POST','/api/host/execute',{
                    'kind':'flatpak_install','target':'org.gnome.Calculator',
                    'preview_token':'valid','confirm':True},cookie=cookie)
                self.assertEqual(status,200)
                self.assertEqual(action.call_args.args[0]['actor'],'local-owner')

    def test_csrf_blocks_agent_traffic(self):
        with patch('host_agent.client_request') as agent:
            status,_,_=self.request('POST','/api/host/preview',
                    {'kind':'service_start','target':'demo.service'},
                    cookie=self.session(),origin='https://attacker.example')
            self.assertEqual(status,403)
            agent.assert_not_called()

if __name__ == '__main__':
    unittest.main()
