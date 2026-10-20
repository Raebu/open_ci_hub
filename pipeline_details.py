"""Native jobs, bounded text logs and artifact links. Never extracts downloaded archives."""
import re
from urllib.parse import quote
from operations import invoke, invoke_json, safe_repo, safe_run_id

ANSI = re.compile(r'\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]')
TOKEN = re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|gl(?:pat|rt)-[A-Za-z0-9_-]+|CCIPAT_[A-Za-z0-9_-]+)')
ASSIGNMENT = re.compile(r'(?i)\b((?:authorization|password|passwd|api[_-]?key|access[_-]?token|secret|[A-Z_]{0,80}TOKEN)\s*[=:]\s*)(?:["\']?)([^\s"\']+)')


def clean_log(value: str) -> str:
    text = ANSI.sub('', value)
    text = re.sub(r'(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+', r'\1 [REDACTED]', text)
    text = TOKEN.sub('[REDACTED]', text)
    text = ASSIGNMENT.sub(r'\1[REDACTED]', text)
    text = re.sub(r'https://[^/\s:@]+:[^/\s@]+@', 'https://[REDACTED]@', text)
    return ''.join(ch for ch in text if ch in '\n\t' or ord(ch) >= 32)[-120_000:]


def _list(value, field=None):
    if field and isinstance(value, dict):
        value = value.get(field)
    if not isinstance(value, list):
        raise RuntimeError('Provider returned an unexpected job/artifact response.')
    return [x for x in value[:100] if isinstance(x, dict)]


def list_jobs(provider: str, repo: str, run_id: str) -> list[dict]:
    repo, run_id = safe_repo(repo), safe_run_id(run_id)
    if provider == 'github':
        rows = _list(invoke_json(['gh', 'api', f'repos/{repo}/actions/runs/{run_id}/jobs?per_page=100']), 'jobs')
        return [{'id': str(x.get('id', '')), 'name': x.get('name', ''),
                 'status': x.get('conclusion') or x.get('status', 'unknown'),
                 'runner': x.get('runner_name') or 'Waiting for worker',
                 'url': x.get('html_url', ''),
                 'failed_steps': [str(step.get('name', ''))[:120]
                                  for step in (x.get('steps') if isinstance(x.get('steps'), list) else [])[:50]
                                  if isinstance(step, dict) and step.get('conclusion') == 'failure'][:3],
                 'failure_reason': str(x.get('failure_reason') or '')[:160]} for x in rows]
    if provider == 'gitlab':
        rows = _list(invoke_json(['glab', 'api', f'projects/{quote(repo, safe="")}/pipelines/{run_id}/jobs?per_page=100']))
        return [{'id': str(x.get('id', '')), 'name': x.get('name', ''), 'status': x.get('status', 'unknown'),
                 'runner': (x.get('runner') or {}).get('description', 'Waiting for worker'),
                 'url': x.get('web_url', ''),
                 'failure_reason': str(x.get('failure_reason') or '')[:160],
                 'failed_steps': []} for x in rows]
    raise ValueError('Job details currently support GitHub and GitLab.')


def job_log(provider: str, repo: str, run_id: str, job_id: str) -> dict:
    repo, run_id, job_id = safe_repo(repo), safe_run_id(run_id), safe_run_id(job_id)
    # Establish pipeline membership before requesting a trace. Prevent confusing
    # cross-pipeline IDs and provide a useful error for an expired/retried job.
    jobs = list_jobs(provider, repo, run_id)
    job = next((x for x in jobs if x['id'] == job_id), None)
    if job is None:
        raise ValueError('Job is not in the first 100 jobs of this pipeline. Open the provider for older/retried jobs.')
    if provider == 'github':
        argv = ['gh', 'api', f'repos/{repo}/actions/jobs/{job_id}/logs']
    else:
        argv = ['glab', 'api', f'projects/{quote(repo, safe="")}/jobs/{job_id}/trace']
    text = invoke(argv)
    return {'text': clean_log(text), 'url': job['url'],
            'note': 'Last 120,000 characters; common credential patterns redacted. Providers may withhold logs until the job completes. Large traces should be opened on the provider.'}


def list_artifacts(provider: str, repo: str, run_id: str) -> list[dict]:
    repo, run_id = safe_repo(repo), safe_run_id(run_id)
    if provider == 'github':
        rows = _list(invoke_json(['gh', 'api', f'repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100']), 'artifacts')
        result = []
        for row in rows:
            aid = safe_run_id(row.get('id'))
            result.append({'name': row.get('name', ''), 'size': row.get('size_in_bytes', 0),
                           'expired': row.get('expired') is True,
                           'url': f'https://github.com/{repo}/actions/runs/{run_id}/artifacts/{aid}'})
        return result
    if provider == 'gitlab':
        rows = _list(invoke_json(['glab', 'api', f'projects/{quote(repo, safe="")}/pipelines/{run_id}/jobs?per_page=100']))
        return [{'name': x['artifacts_file'].get('filename', 'artifacts'),
                 'size': x['artifacts_file'].get('size', 0), 'expired': False,
                 'url': x.get('web_url', '').rstrip('/') + '/artifacts/browse'}
                for x in rows if isinstance(x.get('artifacts_file'), dict) and x['artifacts_file'].get('filename')]
    raise ValueError('Artifact details currently support GitHub and GitLab.')
