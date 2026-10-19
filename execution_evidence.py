#!/usr/bin/env python3
"""Produce a reproducible CI execution receipt, not a green-status assertion.

Receipts are unprivileged runner-observed records. They are NOT independent
cryptographic attestation: a compromised runner can falsify its own output.
See docs/EXECUTION-EVIDENCE.md for trust levels.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

SCHEMA = 'open-ci-hub.execution.v1'
MAX_OUTPUT = 512_000
MAX_DURATION = 600
TESTS = re.compile(r'(?m)^Ran (\d+) tests? in ([\d.]+)s\s*$')
SHA = re.compile(r'^[0-9a-f]{40}$')
SENSITIVE = re.compile(r'(?i)(Bearer\s+\S+|github_pat_\w+|gh[opsur]_\w+|gl(?:pat|rt)-[\w-]+|'
                       r'((?:token|secret|password|api[_-]?key)\s*[=:]\s*)\S+)')

def redact(s: str) -> str:
    def clean(match):
        return match.group(2) + '[REDACTED]' if match.group(2) else '[REDACTED]'
    s = SENSITIVE.sub(clean, s)
    return ''.join(ch for ch in s if ch in '\n\t' or ord(ch) >= 32)[-MAX_OUTPUT:]

def run_step(*, name: str, argv: list[str], directory: Path, timeout: int) -> tuple[dict, str]:
    if not name or len(name) > 80 or not re.fullmatch(r'[A-Za-z0-9_.-]+',name):
        raise ValueError('A short machine-readable step name is required.')
    if not argv or any(not isinstance(x,str) or not x or '\0' in x for x in argv):
        raise ValueError('Command must be a non-empty argument vector.')
    if timeout < 1 or timeout > MAX_DURATION:
        raise ValueError('Invalid step timeout.')
    start = time.monotonic()
    try:
        result = subprocess.run(argv, cwd=directory, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors='replace', timeout=timeout, check=False)
        rc, output = result.returncode, result.stdout
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        rc, output, timed_out = 124, (exc.stdout or b'').decode('utf-8','replace') if isinstance(exc.stdout,bytes) else (exc.stdout or ''), True
    except OSError:
        rc, output, timed_out = 127, 'Command could not start.', False
    elapsed = round(time.monotonic()-start,3)
    cleaned = redact(output)
    if name == 'python-unittest':
        matches = TESTS.findall(cleaned)
        count = int(matches[-1][0]) if matches else 0
        if count < 1 or not re.search(r'(?m)^OK(?: \(.+\))?\s*$', cleaned):
            rc = rc or 1
    else:
        count = None
    line = {'name':name,'argv':argv,'exit_code':rc,'timed_out':timed_out,
            'duration_seconds':elapsed,'observed_test_count':count,
            'log_sha256':hashlib.sha256(cleaned.encode()).hexdigest(),
            'log_bytes':len(cleaned.encode()),'reported_success':rc == 0}
    return line,cleaned

def receipt(*, name, argv, output, directory, timeout):
    output = Path(output)
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise ValueError('Working directory unavailable.')
    if output.suffix != '.json' or output.is_symlink() or output.parent.is_symlink():
        raise ValueError('Unsafe output receipt path.')
    output.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(output.parent,0o700)
    step,log = run_step(name=name,argv=argv,directory=directory,timeout=timeout)
    log_path = output.parent/(name+'.log')
    if log_path.is_symlink():
        raise RuntimeError('Refusing symlinked execution log.')
    log_path.write_text(log,encoding='utf-8')
    os.chmod(log_path,0o600)
    head = os.environ.get('GITHUB_SHA') or os.environ.get('CI_COMMIT_SHA') or ''
    run_id = os.environ.get('GITHUB_RUN_ID') or os.environ.get('CI_PIPELINE_ID') or ''
    data = {'schema':SCHEMA,'source':'runner-reported',
            'verified_independently':False,
            'sha':head if SHA.fullmatch(head) else None,
            'run_id':run_id[:40] if run_id.isdigit() else None,
            'provider':'github' if os.environ.get('GITHUB_ACTIONS') else (
                'gitlab' if os.environ.get('GITLAB_CI') else 'local'),
            'created_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
            'steps':[step], 'all_steps_reported_success':step['reported_success']}
    from hub import secure_write
    secure_write(output,json.dumps(data,indent=2,sort_keys=True)+'\n')
    print(f"Executed {name}: exit {step['exit_code']}, {step['duration_seconds']}s, "
          f"tests observed={step['observed_test_count']}, log digest={step['log_sha256']}",flush=True)
    return data

def verify_files(manifest: Path) -> dict:
    manifest=Path(manifest)
    data=json.loads(manifest.read_text(encoding='utf-8'))
    if not isinstance(data,dict) or data.get('schema')!=SCHEMA or data.get('source')!='runner-reported':
        raise ValueError('Not a supported execution receipt.')
    steps=data.get('steps')
    if not isinstance(steps,list) or not steps:
        raise ValueError('No executed steps were recorded.')
    if any(not isinstance(x,dict) or x.get('reported_success') is not True or
           x.get('exit_code') != 0 for x in steps):
        raise ValueError('Execution receipt contains failed or incomplete steps.')
    for step in steps:
        name=step.get('name')
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+',name):
            raise ValueError('Invalid receipt step.')
        path=manifest.parent/(name+'.log')
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_OUTPUT:
            raise ValueError('Missing or unsafe step transcript.')
        actual=hashlib.sha256(path.read_bytes()).hexdigest()
        if actual!=step.get('log_sha256'):
            raise ValueError('Transcript digest mismatch.')
        if name=='python-unittest' and (not isinstance(step.get('observed_test_count'),int) or step['observed_test_count']<1):
            raise ValueError('No real unittest summary was observed.')
    return {'integrity':'locally-consistent', 'source':'runner-reported',
            'independent_proof':False,'checks':len(steps),
            'message':'Receipt and transcripts match; independent execution not yet attested.'}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='action',required=True)
    run=sub.add_parser('run')
    run.add_argument('--name',required=True)
    run.add_argument('--output',required=True)
    run.add_argument('--cwd',default='.')
    run.add_argument('--timeout',type=int,default=300)
    run.add_argument('command',nargs=argparse.REMAINDER)
    ver=sub.add_parser('verify')
    ver.add_argument('manifest')
    args=p.parse_args(argv)
    try:
        if args.action=='run':
            argv=args.command[1:] if args.command[:1]==['--'] else args.command
            data=receipt(name=args.name,argv=argv,output=args.output,
                         directory=args.cwd,timeout=args.timeout)
            return 0 if data['all_steps_reported_success'] else 1
        print(json.dumps(verify_files(Path(args.manifest)),indent=2))
        return 0
    except (RuntimeError,ValueError,OSError,TypeError,json.JSONDecodeError) as exc:
        print('CI evidence:',exc,file=sys.stderr)
        return 1

if __name__=='__main__':
    raise SystemExit(main())
