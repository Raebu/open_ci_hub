"""Real subprocess stdout caps without restricting permitted file output."""
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from bounded_output import exchange
import managed_worker as worker

class BoundedPipeTests(unittest.TestCase):
    def spawn(self,code):
        return subprocess.Popen([sys.executable,'-c',code],stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE,stderr=subprocess.STDOUT)

    def test_reads_small_json_response(self):
        proc=self.spawn("import sys; sys.stdin.buffer.read(); sys.stdout.write('\"'+'x'*100+'\"')")
        result=exchange(proc,b'{"payload":"safe"}',5,terminate=proc.terminate)
        self.assertEqual(result['reason'],None)
        self.assertEqual(result['output'],b'"'+b'x'*100+b'"')
        self.assertEqual(proc.returncode,0)

    def test_output_is_capped_and_excess_process_is_stopped(self):
        proc=self.spawn("import sys,time; sys.stdout.buffer.write(b'x'*1000000);sys.stdout.flush();time.sleep(10)")
        result=exchange(proc,b'{}',5,terminate=proc.terminate)
        self.assertEqual(result['reason'],'worker_output_limit')
        self.assertLessEqual(len(result['output']),4097)
        self.assertIsNotNone(proc.poll())

    def test_large_local_artifact_does_not_hit_stdout_file_limit(self):
        with tempfile.TemporaryDirectory() as td:
            artifact=str(Path(td)/'generated.bin')
            code=("import sys,pathlib;sys.stdin.buffer.read();"
                  "pathlib.Path("+repr(artifact)+").write_bytes(b'Z'*65536);"
                  "print('{\"ok\":true}')")
            proc=self.spawn(code)
            result=exchange(proc,b'{}',5,terminate=proc.terminate)
            self.assertIsNone(result['reason'])
            self.assertEqual(proc.returncode,0)
            self.assertEqual(Path(artifact).stat().st_size,65536)

    def test_timeout_stops_process_and_preserves_bounded_memory(self):
        proc=self.spawn("import sys,time; sys.stdin.buffer.read();time.sleep(10)")
        result=exchange(proc,b'{}',1,terminate=proc.terminate)
        self.assertEqual(result['reason'],'worker_timeout')
        self.assertIsNotNone(proc.poll())

    def test_invalid_input_fails_without_spawning_extra_work(self):
        proc=self.spawn("import sys; sys.stdin.buffer.read()")
        try:
            with self.assertRaises(ValueError):
                exchange(proc,b'{}',0,terminate=proc.terminate)
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_managed_launcher_does_not_cap_application_artifact_file_size(self):
        job={'job_id':'c'*32,'resources':{'ram_mb':1024,'vram_mb':0,'gpu_index':None}}
        handler={'argv':['/usr/bin/python3','/opt/approved/run.py'],'timeout':60}
        _,command=worker.build_command(job,handler)
        self.assertIn('MemoryMax=1024M',command)
        self.assertIn('MemorySwapMax=0',command)
        self.assertNotIn('/usr/bin/prlimit',command)
        self.assertNotIn('--fsize=',command)

    def test_killed_scope_cannot_ack_without_systemd_confirmation(self):
        with patch('managed_worker.subprocess.run') as stub:
            stub.return_value.returncode=1
            stub.return_value.stdout=''
            self.assertFalse(worker.scope_confirmed_inactive('open-ci-app-'+'c'*32+'.scope'))
        self.assertFalse(worker.scope_confirmed_inactive('any-shell.scope'))

    def test_verified_inactive_scope_is_safe_to_reconcile(self):
        completed=subprocess.CompletedProcess([],0,
             'ActiveState=inactive\nControlGroup=\nLoadState=loaded\n','')
        with patch('managed_worker.subprocess.run',return_value=completed):
            self.assertTrue(worker.scope_confirmed_inactive('open-ci-app-'+'c'*32+'.scope'))

if __name__=='__main__':
    unittest.main()
