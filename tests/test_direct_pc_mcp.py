"""Owner-local direct MCP contract and safe file-creation regression tests."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import direct_pc_mcp as mcp


class DirectMcpTests(unittest.TestCase):
    def request(self, method, rid=1, params=None):
        envelope={'jsonrpc':'2.0','id':rid,'method':method}
        if params is not None:
            envelope['params']=params
        return mcp.mcp_response(envelope)

    def test_tools_discovered_with_explicit_scopes(self):
        response=self.request('tools/list')
        names={x['name'] for x in response['result']['tools']}
        self.assertEqual(names,{ 'get_pc_status', 'inspect_ci_failure', 'create_pc_document',
            'list_coding_workspaces', 'list_coding_files', 'read_coding_file',
            'write_coding_file', 'run_coding_check' })
        for tool in response['result']['tools']:
            self.assertEqual(tool['inputSchema']['additionalProperties'],False)
        self.assertFalse(any('shell' in x['name'] or 'sudo' in x['name'] for x in response['result']['tools']))

    def test_initialize_ping_and_notifications(self):
        v=self.request('initialize')
        self.assertEqual(v['result']['protocolVersion'],'2025-06-18')
        self.assertEqual(self.request('ping')['result'],{})
        self.assertIsNone(mcp.mcp_response({'jsonrpc':'2.0','method':'notifications/initialized'}))

    def test_call_reports_real_tool_result(self):
        with patch('direct_pc_mcp.pc_status',return_value={'host_kind':'live_owner_garuda_linux','disk_free_gb':21.5}) as task:
            result=self.request('tools/call',params={'name':'get_pc_status','arguments':{}})
        task.assert_called_once()
        body=json.loads(result['result']['content'][0]['text'])
        self.assertEqual(body['disk_free_gb'],21.5)
        self.assertFalse(result['result']['isError'])

    def test_rejects_arbitrary_execution_tools(self):
        for name in ('execute_shell','sudo','run_command','read_home_files'):
            reply=self.request('tools/call',params={'name':name,'arguments':{'argv':['/usr/bin/bash']}})
            self.assertTrue(reply['result']['isError'])
            self.assertIn('Unknown MCP tool',reply['result']['content'][0]['text'])

    def test_document_write_requires_enrolment(self):
        with patch('direct_pc_mcp.require_nonroot'),patch('direct_pc_mcp.consent_status',return_value=False):
            with self.assertRaises(PermissionError):
                mcp.create_document('consent.md','Hello')

    def test_document_only_new_private_text_beneath_fixed_directory(self):
        with tempfile.TemporaryDirectory() as root:
            home=Path(root)
            (home/'Documents').mkdir(mode=0o700)
            with patch('direct_pc_mcp.require_nonroot'), \
                 patch('direct_pc_mcp.consent_status',return_value=True), \
                 patch('direct_pc_mcp.Path.home',return_value=home):
                result=mcp.create_document('hello.md','# Verified document')
                self.assertEqual(result['result'],'created')
                path=home/'Documents'/'OpenCIHubTasks'/'hello.md'
                self.assertEqual(path.read_text(),'# Verified document')
                self.assertEqual(path.stat().st_mode & 0o777,0o600)
                with self.assertRaises(FileExistsError):
                    mcp.create_document('hello.md','overwrite')
                for name in ('../steal.txt','../../.bashrc','malicious.py','foo/hi.md','a.txt.tmp'):
                    with self.subTest(name=name), self.assertRaises(ValueError):
                        mcp.create_document(name,'no')
                with self.assertRaises(ValueError):
                    mcp.create_document('too-large.txt','x'*4001)

    def test_symlinked_document_folder_denied(self):
        with tempfile.TemporaryDirectory() as root:
            home=Path(root)
            (home/'Documents').mkdir()
            (home/'Documents'/'OpenCIHubTasks').symlink_to(home)
            with patch('direct_pc_mcp.require_nonroot'), \
                 patch('direct_pc_mcp.consent_status',return_value=True), \
                 patch('direct_pc_mcp.Path.home',return_value=home):
                with self.assertRaises(PermissionError):
                    mcp.create_document('no.md','test')

    def test_user_consent_file_is_private_and_false_by_default(self):
        with tempfile.TemporaryDirectory() as root:
            file=Path(root)/'permissions.json'
            self.assertFalse(mcp.consent_status(file))
            file.write_text(json.dumps({'schema':mcp.SCHEMA,'document_creation':True}))
            file.chmod(0o600)
            self.assertTrue(mcp.consent_status(file))
            file.chmod(0o644)
            with self.assertRaises(PermissionError):
                mcp.consent_status(file)

    def test_stdio_transport_discards_invalid_input_and_never_leaks_stdout(self):
        inputs='\n'.join([
            'not valid json',
            json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{}}),
            json.dumps({'jsonrpc':'2.0','method':'notifications/initialized'}),
            json.dumps({'jsonrpc':'2.0','id':2,'method':'tools/list'}),
        ])+'\n'
        output=io.StringIO()
        with patch('direct_pc_mcp.require_nonroot'):
            mcp.serve(io.StringIO(inputs),output)
        items=[json.loads(v) for v in output.getvalue().splitlines()]
        self.assertEqual(len(items),2)
        self.assertEqual([x['id'] for x in items],[1,2])

    def test_github_failure_lookup_calls_read_only_native_inspection(self):
        import sys,types
        module=types.ModuleType('ci_failure_diagnostics')
        module.diagnose=lambda provider,repo,run,include_logs=False: {
            'provider':provider,'repo':repo,'run':run,'logs_checked':include_logs}
        with patch.dict(sys.modules,{'ci_failure_diagnostics':module}):
            actual=mcp.invoke_tool('inspect_ci_failure',{
                'provider':'github','repository':'Raebu/open_ci_hub','run_id':'39460692725'})
        self.assertEqual(actual['repo'],'Raebu/open_ci_hub')
        self.assertTrue(actual['logs_checked'])
        with self.assertRaises(ValueError):
            mcp.invoke_tool('inspect_ci_failure',{
                'provider':'github','repository':'../../etc','run_id':'1'})


if __name__ == '__main__':
    unittest.main()
