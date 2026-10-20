"""Bound a worker's combined stdout/stderr without limiting other file writes.

Unlike RLIMIT_FSIZE, this only controls the stdout pipe. Process termination
and acknowledgement are the caller's responsibility; an uncertain kill must
leave the job's lease reserved rather than releasing resources.
"""
from __future__ import annotations
import threading
import subprocess

MAX_OUTPUT_BYTES=4096
MAX_OUTPUT_READ=8192

def exchange(process, payload: bytes, timeout: int, terminate, max_bytes: int = MAX_OUTPUT_BYTES):
    """Return output/reason; never store more than max_bytes+1 bytes.

    terminate is called on excess output or timeout and must target the
    whole managed user scope, not just its systemd-run parent.
    """
    if (not isinstance(payload,bytes) or type(timeout)!=int or not 1<=timeout<=1800
        or type(max_bytes)!=int or not 1<=max_bytes<=MAX_OUTPUT_BYTES):
        raise ValueError('Invalid bounded process I/O configuration.')
    if process.stdin is None or process.stdout is None:
        raise RuntimeError('Managed child must have stdin and stdout pipes.')
    chunks=bytearray()
    too_much=threading.Event()
    errors=[]
    def drain():
        try:
            while True:
                method=getattr(process.stdout,'read1',process.stdout.read)
                chunk=method(MAX_OUTPUT_READ)
                if not chunk:break
                remaining=max_bytes+1-len(chunks)
                if remaining>0:chunks.extend(chunk[:remaining])
                if len(chunks)>max_bytes:
                    too_much.set()
                    terminate()
                    break
        except (OSError,ValueError) as exc:
            errors.append(type(exc).__name__)
            terminate()
    reader=threading.Thread(target=drain,name='app-worker-output',daemon=True)
    reader.start()
    try:
        try:
            process.stdin.write(payload)
            process.stdin.flush()
        except (BrokenPipeError,OSError):
            pass
        finally:
            process.stdin.close()
        timed_out=False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out=True
            terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:pass
        reader.join(timeout=5)
        if reader.is_alive():
            terminate()
            return {'output':bytes(chunks),'reason':'output_pipe_did_not_close'}
        if errors:return {'output':bytes(chunks),'reason':'output_reader_error'}
        if too_much.is_set():return {'output':bytes(chunks),'reason':'worker_output_limit'}
        if timed_out:return {'output':bytes(chunks),'reason':'worker_timeout'}
        return {'output':bytes(chunks),'reason':None}
    finally:
        if not reader.is_alive():
            process.stdout.close()
