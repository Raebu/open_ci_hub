"""Provider-side execution evidence inventory. Never equate green status with proof."""
from __future__ import annotations
from urllib.parse import quote
from operations import invoke_json, safe_repo, safe_run_id

def inspect(provider: str, repo: str, run_id: str) -> dict:
    repo= safe_repo(repo)
    run_id= safe_run_id(run_id)
    if provider == 'github':
        data=invoke_json(['gh','api',f'repos/{repo}/actions/runs/{run_id}'])
        jobs=invoke_json(['gh','api',f'repos/{repo}/actions/runs/{run_id}/jobs?per_page=100'])
        artifacts=invoke_json(['gh','api',f'repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100'])
        if not isinstance(data,dict) or not isinstance(jobs,dict) or not isinstance(jobs.get('jobs'),list) or not isinstance(artifacts,dict) or not isinstance(artifacts.get('artifacts'),list):
            raise RuntimeError('Cannot confirm native GitHub pipeline evidence.')
        units=[]
        for x in jobs['jobs'][:100]:
            if not isinstance(x,dict):
                continue
            steps=x.get('steps')
            units.append({'name':str(x.get('name',''))[:100],
                'status':str(x.get('conclusion') or x.get('status') or 'unknown')[:40],
                'runner':str(x.get('runner_name') or '')[:100],
                'duration_seconds':_duration(x),
                'steps': [{'name':str(y.get('name',''))[:90], 'status':str(y.get('conclusion') or y.get('status'))[:32]}
                          for y in (steps if isinstance(steps,list) else [])[:50] if isinstance(y,dict)]})
        receipt=any(x.get('name')=='open-ci-hub-execution-proof' and x.get('expired') is False and x.get('size_in_bytes',0)>0 for x in artifacts['artifacts'] if isinstance(x,dict))
        return {'provider':provider,'repo':repo,'run':run_id,
            'commit':str(data.get('head_sha',''))[:40],
            'pipeline_status':str(data.get('conclusion') or data.get('status'))[:40],
            'jobs':units,'receipt_artifact_available':receipt,
            'verification':'runner-reported' if receipt else 'not-proven',
            'independent_attestation':False,
            'message':('A self-hosted execution receipt artifact exists. Download and verify its log digest; '
                      'it is runner-reported, not independent proof.'
                      if receipt else 'No recorded execution receipt found. Green alone is not proof.')}

    if provider == 'gitlab':
        slug=quote(repo,safe='')
        pipeline=invoke_json(['glab','api',f'projects/{slug}/pipelines/{run_id}'])
        jobs=invoke_json(['glab','api',f'projects/{slug}/pipelines/{run_id}/jobs?per_page=100'])
        if not isinstance(pipeline,dict) or not isinstance(jobs,list):
            raise RuntimeError('Cannot confirm native GitLab pipeline evidence.')
        units=[]
        for x in jobs[:100]:
            if not isinstance(x,dict):
                continue
            runner=x.get('runner') if isinstance(x.get('runner'),dict) else None
            units.append({'name':str(x.get('name',''))[:100],
                'status':str(x.get('status','unknown'))[:40],
                'runner':str((runner or {}).get('description',''))[:100],
                'failure_reason':str(x.get('failure_reason') or '')[:60],
                'duration_seconds':x.get('duration'),
                'steps':[]})
        return {'provider':provider,'repo':repo,'run':run_id,
            'commit':str(pipeline.get('sha',''))[:40],
            'pipeline_status':str(pipeline.get('status','unknown'))[:40],
            'jobs':units,'receipt_artifact_available':False,
            'verification':'not-proven','independent_attestation':False,
            'message':'No independently validated job-by-job execution proof; inspect native job trace and runner assignment.'}
    raise ValueError('Execution inspection supports GitHub and GitLab.')

def _duration(item):
    from datetime import datetime
    def parse(s):
        if not isinstance(s,str):
            return None
        try:
            return datetime.fromisoformat(s.replace('Z','+00:00'))
        except ValueError:
            return None
    a,b=parse(item.get('started_at')),parse(item.get('completed_at'))
    return round((b-a).total_seconds(),3) if a and b and b>=a else None
