"""Provider-native failure reasons without disclosing raw logs or credentials."""
from __future__ import annotations

from pipeline_details import list_jobs, clean_log
from operations import safe_repo, safe_run_id

FAILED = {'failure', 'failed', 'timed_out', 'startup_failure', 'stuck'}
WAITING = {'queued', 'pending', 'requested', 'waiting', 'in_progress', 'running'}
STEPS_LIMIT = 3


def diagnose(provider: str, repo: str, run_id: str) -> dict:
    repo, run_id = safe_repo(repo), safe_run_id(run_id)
    if provider not in ('github', 'gitlab'):
        raise ValueError('Failure inspection supports GitHub and GitLab.')
    jobs = list_jobs(provider, repo, run_id)
    failures = []
    for row in jobs:
        state = str(row.get('status') or 'unknown').lower()
        if state not in FAILED:
            continue
        # Job names and failure reason labels are user/provider controlled:
        # strip control chars and common secret patterns, and bound their size.
        name = clean_log(str(row.get('name') or 'Unnamed job'))[:100]
        failed_steps = [clean_log(str(s))[:120] for s in row.get('failed_steps', [])[:STEPS_LIMIT]]
        reason = clean_log(str(row.get('failure_reason') or ''))[:160]
        if not reason and failed_steps:
            reason = 'Step failed: ' + '; '.join(failed_steps)
        if not reason:
            reason = 'Provider marked this job failed, but did not include a cause in the jobs API.'
        failures.append({'job_id': row['id'], 'job': name, 'state': state,
                         'reason': reason, 'failed_steps': failed_steps,
                         'runner': clean_log(str(row.get('runner') or ''))[:100],
                         'url': row.get('url') or ''})
    if failures:
        outcome = ('Failure detected in ' + str(len(failures)) + ' native job(s). '
                   'Use the job log for the full redacted trace; do not retry until the reason is understood.')
        code = 'native_job_failed'
    elif not jobs:
        outcome = ('The provider has not returned any jobs for this run. '
                   'Check workflow matching, runner eligibility, branch rules and provider status.')
        code = 'jobs_not_created'
    elif all(str(j.get('status', '')).lower() in WAITING for j in jobs):
        outcome = ('Jobs are queued/running; no completed failure cause is available yet. '
                   'Check self-hosted runner online/label status and wait for completion.')
        code = 'jobs_not_finished'
    else:
        outcome = 'No failed native jobs appear in the provider response; inspect the run-level conclusion and workflow policy.'
        code = 'no_failed_native_jobs'
    return {'provider': provider, 'repo': repo, 'run': run_id,
            'code': code, 'message': outcome, 'failures': failures[:10],
            'jobs_seen': len(jobs), 'raw_logs_included': False}
