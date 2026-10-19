"""Host-PTY lifecycle: opt-in, actor isolation, denial, expiry and HTTP CSRF."""
import base64
import os
import time
import unittest
from unittest.mock import patch
import host_agent as agent
from host_terminal import TerminalManager

POLICY={'flatpaks':[], 'services':[], 'mobile_admins':['editor@example.com'],
        'terminal_enabled':True, 'terminal_admins':['editor@example.com']}

class PolicyTests(unittest.TestCase):
    def test_terminal_disabled_by_default_and_requires_local_enable(self):
        with patch('host_agent.policy_data',return_value={**POLICY,'terminal_enabled':False}):
            a=agent.HostController()
            try:
                with self.assertRaises(PermissionError):
                    a.process({'op':'terminal_preview','actor':'local-owner'})
            finally:a.terminal.shutdown()

    def test_mobile_editor_not_equivalent_to_host_terminal_approval(self):
        with patch('host_agent.policy_data',return_value={**POLICY,'terminal_admins':[]}):
            a=agent.HostController()
            try:
                with self.assertRaises(PermissionError):
                    a.process({'op':'terminal_preview','actor':'editor@example.com'})
            finally:a.terminal.shutdown()

    def test_preview_one_time_and_same_actor(self):
        with patch('host_agent.policy_data',return_value=POLICY), \
             patch('host_agent._append_audit'), \
             patch('host_terminal.TerminalManager.open',return_value={'session_id':'sid'}) as opened:
            a=agent.HostController()
            try:
                token=a.process({'op':'terminal_preview','actor':'editor@example.com'})['preview_token']
                with self.assertRaises(PermissionError):
                    a.process({'op':'terminal_open','actor':'local-owner','preview_token':token})
                token=a.process({'op':'terminal_preview','actor':'editor@example.com'})['preview_token']
                self.assertEqual(a.process({'op':'terminal_open','actor':'editor@example.com',
                                            'preview_token':token})['session_id'],'sid')
                with self.assertRaises(PermissionError):
                    a.process({'op':'terminal_open','actor':'editor@example.com',
                               'preview_token':token})
                opened.assert_called_once_with('editor@example.com')
            finally:a.terminal.shutdown()

    def test_no_terminal_for_unauthorised_operations(self):
        a=agent.HostController()
        try:
            for op in ('terminal_open','terminal_read','terminal_write','terminal_close'):
                with self.subTest(op=op), patch('host_agent.policy_data',return_value={**POLICY,'terminal_enabled':False}), \
                     self.assertRaises(PermissionError):
                    a.process({'op':op,'actor':'local-owner'})
        finally:a.terminal.shutdown()

class PTYTests(unittest.TestCase):
    def test_new_host_shell_executes_only_as_local_user_and_enforces_actor(self):
        if os.name!='posix' or not os.path.isfile('/usr/bin/bash') or os.geteuid()==0:
            self.skipTest('Non-root Linux bash required')
        manager=TerminalManager()
        try:
            session=manager.open('local-owner')['session_id']
            with self.assertRaises(PermissionError):
                manager.read('editor@example.com',session)
            with self.assertRaises(ValueError):
                manager.write('local-owner',session,base64.b64encode(b'x'*600).decode())
            cmd=base64.b64encode(b"printf 'OpenCIHubPTYVerified-42\\n'\n").decode()
            manager.write('local-owner',session,cmd)
            output=''
            for i in range(100):
                output+=base64.b64decode(manager.read('local-owner',session)['data_b64']).decode('utf-8','replace')
                if 'OpenCIHubPTYVerified-42' in output:break
                time.sleep(.03)
            self.assertIn('OpenCIHubPTYVerified-42',output)
            with self.assertRaises(RuntimeError):
                manager.open('local-owner')
            self.assertTrue(manager.close('local-owner',session)['closed'])
            with self.assertRaises(PermissionError):
                manager.read('local-owner',session)
        finally:manager.shutdown()

    def test_expired_session_is_closed_without_browser_polling(self):
        if os.name!='posix' or not os.path.isfile('/usr/bin/bash') or os.geteuid()==0:
            self.skipTest('Non-root Linux bash required')
        manager=TerminalManager()
        try:
            session=manager.open('local-owner')['session_id']
            manager.sessions[session]['last']-=301
            manager._expire()
            self.assertNotIn(session,manager.sessions)
        finally:manager.shutdown()

from tests.test_web_console import DashboardHTTPTests

class TerminalHTTPTests(DashboardHTTPTests):
    def test_terminal_does_not_run_for_unauthenticated_requests(self):
        with patch('host_agent.client_request') as client:
            status,_,_=self.request('POST','/api/host/terminal/preview',{})
            self.assertEqual(status,401)
            client.assert_not_called()

    def test_terminal_requires_csrf_and_confirmation(self):
        session=self.session()
        with patch('host_agent.client_request') as client:
            status,_,_=self.request('POST','/api/host/terminal/open',
                      {'preview_token':'X','confirm':True},cookie=session,
                      origin='https://wrong.example')
            self.assertEqual(status,403)
            status,_,_=self.request('POST','/api/host/terminal/open',
                      {'preview_token':'X'},cookie=session)
            self.assertEqual(status,400)
            client.assert_not_called()

    def test_owner_terminal_goes_to_agent_not_host_subprocess(self):
        with patch('host_agent.client_request',return_value={
             'preview_token':'stub','expires_in':60}) as client:
            status,data,_=self.request('POST','/api/host/terminal/preview',{},
                                       cookie=self.session())
            self.assertEqual(status,200)
            self.assertEqual(client.call_args.args[0],{'op':'terminal_preview','actor':'local-owner'})

if __name__=='__main__':
    unittest.main()
