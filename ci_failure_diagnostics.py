"""Provider-native CI failure evidence, preserving exact redacted log lines.

The GitHub/GitLab jobs and logs are provider-owned evidence. Diagnostic regexes
select likely error lines; they do not invent root causes. No job is rerun.
"""
from __future__ import annotations

import re
from urllib.parse import quote
from operations import invoke_json, safe_repo, safe_run_id
from pipeline_details import clean_log, job_log, list_jobs

FAILED = {'failure', 'failed', 'timed_out', 'startup_failure', 'stuck'}
WAITING = {'queued', 'pending', 'requested', 'waiting', 'in_progress', 'running'}
STEPS_LIMIT = 3
MAX_LOG_JOBS = 3
MAX_EXCERPT_CHARS = 6000
MAX_ERROR_MARKERS = 8
MARKER = re.compile(
    r'(?i)(::error::|(?:^|\\s)(?:error:|fatal:|FAIL(?:ED)?(?:\\s|:)|'
    r'AssertionError|Traceback \\(most recent call last\\)|'
    r'[A-Z_]*Exception:|npm ERR!|error TS[0-9]{4}|'
    r'Process completed with exit code [1-9][0-9]*|'
    r'command returned [1-9][0-9]*|Job failed|'
    r'ERROR:)))'
)
EXIT = re.compile(
    r'(?i)(?:process completed with exit code|(?:command )?returned|'
    r'exited? with (?:exit )?(?:status|code))\\s*:?\\s*([1-9][0-9]{0,3})\\b'
)


def _evidence_excerpt(transcript: str) -> dict:
    """Use the provider's real lines, with the same redaction as /api/job-log."""
    lines = clean_log(transcript).splitlines()
    matches = [i for i, row in enumerate(lines) if MARKER.search(row)]
    chosen = set()
    for i in matches[-MAX_ERROR_MARKERS:]:
        chosen.update(range(max(0, i-1), min(len(lines), i+3)))
    if not chosen:
        chosen.update(range(max(0, len(lines)-15), len(lines)))
    selected = [f'{i + 1}: {lines[i]}' for i in sorted(chosen)]
    excerpt = '\\n'.join(selected)
    if len(excerpt) > MAX_EXCERPT_CHARS:
        excerpt = excerpt[-MAX_EXCERPT_CHARS:]
    code = EXIT.search(clean_log(transcript))
    return {'log_excerpt': excerpt,
            'excerpt_kind': 'error_context' if matches else 'last_output_lines',
            'log_line_count': len(lines),
            'exit_code_from_log': int(code.group(1)) if code else None,
            'excerpt_is_sanitized_verbatim': True,
            'excerpt_truncated': len(selected) < len(lines) or len(excerpt) >= MAX_EXCERPT_CHARS}


def _metadata(provider: str, repo: str, run_id: str) -> dict:
    """Read-only commit/run provenance. If unavailable, say so explicitly."""
    try:
        if provider == 'github':
            meta = invoke_json(['gh','api',f'repos/{repo}/actions/runs/{run_id}'])
            if not isinstance(meta, dict):
                raise RuntimeError('Invalid run metadata.')
            sha = meta.get('head_sha')
            return {'commit': sha if isinstance(sha, str) and re.fullmatch('[a-f0-9]{40}', sha) else '',
                    'pipeline_status': str(meta.get('conclusion') or meta.get('status') or 'unknown')[:40],
                    'run_url': f'https://github.com/{repo}/actions/runs/{run_id}',
                    'attempt': meta.get('run_attempt') if isinstance(meta.get('run_attempt'),int) else None}
        meta = invoke_json(['glab','api',f'projects/{quote(repo, safe="")}/pipelines/{run_id}'])
        if not isinstance(meta,dict):
            raise RuntimeError('Invalid pipeline metadata.')
        sha = meta.get('sha')
        return {'commit': sha if isinstance(sha,str) and re.fullmatch('[a-f0-9]{40}',sha) else '',
                'pipeline_status': str(meta.get('status') or 'unknown')[:40],
                'run_url': str(meta.get('web_url') or '')[:300],
                'attempt': None}
    except (RuntimeError, ValueError):
        return {'commit': '', 'pipeline_status': 'unverified',
                'run_url': f'https://github.com/{repo}/actions/runs/{run_id}' if provider == 'github' else '',
                'attempt': None}


def diagnose(provider: str, repo: str, run_id: str, *, include_logs: bool = False) -> dict:
    repo, run_id = safe_repo(repo), safe_run_id(run_id)
    if provider not in ('github', 'gitlab'):
        raise ValueError('Failure inspection supports GitHub and GitLab.')
    jobs = list_jobs(provider, repo, run_id)
    failures = []
    for row in jobs:
        state = str(row.get('status') or 'unknown').lower()
        if state not in FAILED:
            continue
        name = clean_log(str(row.get('name') or 'Unnamed job'))[:100]
        failed_steps = [clean_log(str(s))[:120] for s in row.get('failed_steps', [])[:STEPS_LIMIT]]
        reason = clean_log(str(row.get('failure_reason') or ''))[:160]
        if not reason and failed_steps:
            reason = 'Provider reports failure in step: ' + '; '.join(failed_steps)
        if not reason:
            reason = ('Provider marked this job failed but did not expose a failing step '
                      'or an error reason in its jobs API.')
        runner = str(row.get('runner') or '')
        item = {'job_id': row['id'], 'job': name, 'state': state,
                'reason': reason, 'failed_steps': failed_steps,
                'runner': clean_log(runner)[:100],
                'runner_assigned': bool(runner and runner != 'Waiting for worker'),
                'url': str(row.get('url') or '')[:400],
                'log_state': 'not_requested', 'log_excerpt': '',
                'log_note': 'Open Jobs, logs & artifacts for the native transcript.',
                'exit_code_from_log': None}
        if include_logs and len(failures) < MAX_LOG_JOBS:
            try:
                payload = job_log(provider, repo, run_id, str(row['id']))
                transcript = payload.get('text')
                if not isinstance(transcript, str) or not transcript.strip():
                    raise RuntimeError('The provider returned an empty transcript.')
                item.update(_evidence_excerpt(transcript))
                item['log_state'] = 'available'
                item['log_note'] = ('This is original provider output, with common secrets redacted. '
                                    'The excerpt selects likely failing lines; open the native job '
                                    'for the full transcript.')
            except (RuntimeError, ValueError, OSError):
                item['log_state'] = 'unavailable'
                item['log_note'] = ('The provider did not return the job transcript. The exact '
                                    'failure text is unavailable; this is NOT proof the job ran '
                                    'and NOT an inferred code error.')
        failures.append(item)
    if failures:
        code = 'native_job_failed'
        message = ('Provider reports ' + str(len(failures)) + ' failed native job(s). '
                   'Review the preserved failure excerpt when available.')
    elif not jobs:
        code = 'jobs_not_created'
        message = ('The provider returned no jobs. This does not establish a test failure: '
                   'check trigger, branch filters, runner registration, labels and plan limits.')
    elif all(str(j.get('status', '')).lower() in WAITING for j in jobs):
        code = 'jobs_not_finished'
        message = ('Jobs have not finished. There is no completed failure transcript yet; '
                   'check runner assignment and provider queue.')
    else:
        code = 'no_failed_native_jobs'
        message = ('No failed native jobs appear in the provider response. '
                   'A run-level or external commit status can still have failed.')
    provenance = _metadata(provider, repo, run_id) if include_logs else {
        'commit': '', 'pipeline_status': 'not_requested', 'run_url': '',
        'attempt': None}
    return {'provider': provider, 'repo': repo, 'run': run_id,
            'code': code, 'message': message, 'failures': failures[:10],
            'jobs_seen': len(jobs), 'logs_checked': include_logs,
            'raw_logs_included': False,
            **provenance}
