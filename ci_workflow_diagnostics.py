"""Structured, read-only reasons for native GitHub runner workflow refusal.

Strict admission remains deny-by-default. This module does NOT dispatch jobs,
edit CI YAML, access runner tokens, or weaken GitHub PR trust restrictions.
"""
from __future__ import annotations
import re
from urllib.parse import quote

import fleet
from operations import safe_repo

LOCAL_LABELS = '[self-hosted, linux, local-ci]'
RUNS_ON = re.compile(r'(?m)^ {4}runs-on:\s*([^\n#]+)')
JOB_FEATURE = re.compile(r'(?m)^ {4}(?:uses|container|services):')
PULL_EVENT = re.compile(r'(?m)^  (?:pull_request|pull_request_target):')
MAX_REASONS = 25


def _issue(path, code, detail, remedy):
    return {'workflow': path, 'code': code, 'reason': detail,
            'next_step': remedy}


def inspect_github(repo: str, branch: str) -> dict:
    repo = safe_repo(repo)
    if len(repo.split('/')) != 2 or not isinstance(branch, str) or not fleet.BRANCH.fullmatch(branch) or '..' in branch:
        raise ValueError('Provide GitHub OWNER/REPO and an existing safe branch name.')
    result = {'repo': repo, 'branch': branch, 'verified': False,
              'code': '', 'message': '', 'workflows': [], 'issues': [],
              'suggestion': ''}
    tree = fleet._json_object(
        ['gh', 'api', f'repos/{repo}/git/trees/{quote(branch, safe="")}?recursive=1'])
    if tree.get('truncated') is not False or not isinstance(tree.get('tree'), list):
        result.update(code='workflow_tree_unverified',
                      message='The repository workflow inventory is incomplete or unavailable.',
                      suggestion='Check GitHub permissions and retry; do not infer a missing CI file.')
        return result
    files = sorted({row['path'] for row in tree['tree']
                    if isinstance(row, dict) and isinstance(row.get('path'), str)
                    and row.get('type', 'blob') == 'blob'
                    and row['path'].startswith('.github/workflows/')
                    and row['path'].endswith(('.yml', '.yaml'))})
    result['workflows'] = files[:fleet.MAX_FILES]
    if not files:
        result.update(code='workflow_missing',
                      message='No GitHub Actions YAML file exists under .github/workflows/ on the checked branch.',
                      suggestion='Use Native CI onboarding to propose a new push-only self-hosted workflow; review and merge it first.')
        return result
    if len(files) > fleet.MAX_FILES:
        result.update(code='workflow_inventory_too_large',
                      message='More workflow files exist than the audited runner-pool limit.',
                      suggestion='Review the full workflow inventory manually; no automatic runner admission.')
        return result
    eligible_jobs = 0
    for path in files:
        try:
            body, _ = fleet._file(repo, branch, path)
        except (RuntimeError, ValueError):
            result['issues'].append(_issue(path, 'workflow_unreadable',
                'Cannot verify the contents or integrity of this workflow.',
                'Verify the file is readable and at most 64 KiB; re-run the workflow audit.'))
            continue
        jobs = [match.strip() for match in RUNS_ON.findall(body)]
        if not fleet.event_is_trusted_main(body, branch):
            event = 'An untrusted pull-request trigger exists.' if PULL_EVENT.search(body) else 'Push event or branch filter is not strictly the checked branch.'
            result['issues'].append(_issue(path, 'trigger_not_allowlisted', event,
                'Use a separate default-branch-only push workflow for personal runners; isolate untrusted PR builds.'))
        if JOB_FEATURE.search(body):
            result['issues'].append(_issue(path, 'job_isolation_unverified',
                'A reusable, container or service job needs manual security review.',
                'Review the entire job environment rather than silently admitting it.'))
        if not jobs:
            result['issues'].append(_issue(path, 'no_static_runner',
                'No static four-space runs-on entry can be verified.',
                'Specify explicit [self-hosted, linux, local-ci] labels on the intended job.'))
        for label in jobs:
            if label != LOCAL_LABELS:
                result['issues'].append(_issue(path, 'runner_label_mismatch',
                    'At least one job is not pinned to [self-hosted, linux, local-ci].',
                    'Review and migrate that job separately; do not silently switch hosted minutes.'))
            else:
                eligible_jobs += 1
    if result['issues']:
        result['issues'] = result['issues'][:MAX_REASONS]
        first = result['issues'][0]
        result.update(code=first['code'],
                      message=f"Runner-pool workflow gate blocked: {first['reason']}",
                      suggestion=first['next_step'])
        return result
    if eligible_jobs == 0:
        result.update(code='no_eligible_job',
                      message='Workflows exist but none contains an eligible local-ci job.',
                      suggestion='Add and review a self-hosted push workflow.')
        return result
    result.update(verified=True, code='eligible',
                  message=f'{eligible_jobs} statically eligible self-hosted job(s) found across {len(files)} workflow(s).',
                  suggestion='Check runner registration, exact label assignment and native job execution independently.')
    return result
