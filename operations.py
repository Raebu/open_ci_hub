"""Provider-neutral, bounded command facade for the local control plane.

The hub orchestrates each provider's native CI; it does not execute remote
code or implement an interchangeable CI executor. All CLI arguments are arrays.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from urllib.parse import quote

SLUG = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}(?:/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}){1,9}$')
OWNER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')
MAX_RESULT = 240_000


def safe_repo(value: str) -> str:
    if not isinstance(value, str) or len(value) > 300 or not SLUG.fullmatch(value) or any(part.endswith('.') or '..' in part for part in value.split('/')):
        raise ValueError('Repository must be an owner/repository or group/subgroup/project path.')
    return value


def safe_run_id(value) -> str:
    value = str(value)
    if not value.isdecimal() or not (1 <= int(value) < 10 ** 18):
        raise ValueError('A positive numeric workflow or pipeline ID is required.')
    return value


def invoke(argv: list[str], *, timeout: int = 30) -> str:
    if argv[0] not in ('gh', 'glab', 'circleci'):
        raise ValueError('Unsupported provider CLI.')
    if not shutil.which(argv[0]):
        raise RuntimeError(f'Install and sign in to the {argv[0]} CLI on this computer.')
    try:
        process = subprocess.run(argv, text=True, capture_output=True, timeout=timeout,
                                 check=False, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise RuntimeError('Provider operation timed out; check the provider before retrying.') from None
    if process.returncode:
        # Do not echo stderr: host CLI errors can include private repository data.
        raise RuntimeError(f'{argv[0]} returned an error. Check CLI sign-in and repository permissions.')
    if len(process.stdout) > MAX_RESULT:
        raise RuntimeError('Provider response was too large to display safely.')
    return process.stdout


def invoke_json(argv: list[str]):
    try:
        result = json.loads(invoke(argv))
    except json.JSONDecodeError:
        raise RuntimeError('Provider CLI returned invalid JSON.') from None
    if not isinstance(result, (list, dict)):
        raise RuntimeError('Provider returned an unexpected response.')
    return result


def list_repositories(provider: str, owner: str = '') -> list[dict]:
    if provider == 'github':
        if owner and (not OWNER.fullmatch(owner) or '..' in owner):
            raise ValueError('Invalid GitHub owner.')
        args = ['gh', 'repo', 'list']
        if owner:
            args.append(owner)
        args += ['--limit', '50', '--json', 'nameWithOwner,description,isPrivate,url']
        raw = invoke_json(args)
        if not isinstance(raw, list):
            raise RuntimeError('Unexpected GitHub repository response.')
        return [{'name': x.get('nameWithOwner',''), 'description': x.get('description','') or '',
                 'private': bool(x.get('isPrivate')), 'url': x.get('url','')} for x in raw[:50] if isinstance(x,dict)]
    if provider == 'gitlab':
        if owner:
            raise ValueError('GitLab repository inventory shows projects you are a member of; owner filtering is not implemented.')
        raw = invoke_json(['glab', 'repo', 'list', '--member', '--output', 'json', '--per-page', '50'])
        if isinstance(raw, dict):
            raw = raw.get('projects', raw.get('items', []))
        if not isinstance(raw, list):
            raise RuntimeError('Unexpected GitLab projects response.')
        return [{'name': x.get('path_with_namespace') or x.get('fullPath') or x.get('name_with_namespace',''),
                 'description': x.get('description','') or '',
                 'private': x.get('visibility') != 'public',
                 'url': x.get('web_url') or x.get('webUrl') or ''} for x in raw[:50] if isinstance(x,dict)]
    raise ValueError('Repository browsing is supported for GitHub and GitLab.')


def list_pipelines(provider: str, repo: str) -> list[dict]:
    repo = safe_repo(repo)
    if provider == 'circleci':
        from providers.circleci_cockpit import list_pipelines as circle_pipelines
        return circle_pipelines(repo)
    if provider == 'github':
        raw = invoke_json(['gh','run','list','--repo',repo,'--limit','20','--json',
                           'databaseId,displayTitle,status,conclusion,headBranch,createdAt,url'])
        if not isinstance(raw,list):
            raise RuntimeError('Unexpected GitHub workflow response.')
        return [{'id': str(x.get('databaseId','')), 'title': x.get('displayTitle',''),
                 'status': x.get('conclusion') or x.get('status') or 'unknown',
                 'branch': x.get('headBranch',''), 'created': x.get('createdAt',''),
                 'url': x.get('url','')} for x in raw[:20] if isinstance(x,dict)]
    if provider == 'gitlab':
        raw = invoke_json(['glab','ci','list','--repo',repo,'--output','json','--per-page','20'])
        if isinstance(raw,dict):
            raw = raw.get('pipelines', raw.get('items', []))
        if not isinstance(raw,list):
            raise RuntimeError('Unexpected GitLab pipeline response.')
        return [{'id': str(x.get('id','')), 'title': x.get('name') or f"Pipeline #{x.get('id','')}",
                 'status': x.get('status','unknown'), 'branch': x.get('ref',''),
                 'created': x.get('created_at',''), 'url': x.get('web_url','')}
                for x in raw[:20] if isinstance(x,dict)]
    raise ValueError('Pipeline management is supported for GitHub and GitLab.')


def pipeline_action(provider: str, repo: str, run_id, action: str) -> None:
    repo = safe_repo(repo)
    if action == 'retry':
        from cost_policy import deny_unverified_external_work
        deny_unverified_external_work('provider pipeline retry')
    if provider == 'circleci':
        from providers.circleci_cockpit import pipeline_action as circle_action
        return circle_action(repo, run_id, action)
    run_id = safe_run_id(run_id)
    if action not in ('cancel','retry'):
        raise ValueError('Unsupported pipeline action.')
    if provider == 'github':
        args = ['gh','run','cancel' if action == 'cancel' else 'rerun',run_id,'--repo',repo]
    elif provider == 'gitlab':
        if action == 'cancel':
            args = ['glab','ci','cancel','pipeline',run_id,'--repo',repo]
        else:
            # Use the official GitLab retry REST endpoint with glab's authenticated transport.
            args = ['glab','api','-X','POST',f'projects/{quote(repo, safe="")}/pipelines/{run_id}/retry']
    else:
        raise ValueError('Pipeline management is supported for GitHub and GitLab.')
    invoke(args, timeout=30)


def system_health() -> dict:
    import os
    from pathlib import Path
    from hub import docker_ready
    disk = shutil.disk_usage(Path.home())
    return {'docker': docker_ready(), 'cpus': os.cpu_count(),
            'disk_free_gb': round(disk.free / 1024 ** 3, 1),
            'disk_total_gb': round(disk.total / 1024 ** 3, 1),
            'provider_binaries': {name: bool(shutil.which(binary)) for name,binary in
                                  [('github','gh'),('gitlab','glab'),('circleci','circleci')]}}
