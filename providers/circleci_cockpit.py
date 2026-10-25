"""CircleCI CLI 1.7+ native workflow cockpit. No universal pipeline emulation."""
import re
from operations import invoke_json, invoke, safe_repo

UUID = re.compile(r'^[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}$')


def workflow_id(value):
    if not isinstance(value, str) or not UUID.fullmatch(value):
        raise ValueError('CircleCI requires a workflow UUID.')
    return value.lower()


def project(repo):
    repo = safe_repo(repo)
    if len(repo.split('/')) != 2:
        raise ValueError('CircleCI cockpit requires a GitHub OWNER/REPO source.')
    return 'gh/' + repo


def workflows(repo):
    raw = invoke_json(['circleci', 'workflow', 'list', '--project', project(repo), '--limit', '20', '--json'])
    if not isinstance(raw, list):
        raise RuntimeError('Unexpected CircleCI workflow response. Install the current official CircleCI CLI.')
    return [x for x in raw[:100] if isinstance(x, dict)]


def list_pipelines(repo):
    return [{'id': workflow_id(x.get('id')), 'title': x.get('name', 'Workflow'),
             'status': x.get('outcome') or x.get('current_outcome') or x.get('phase') or 'unknown',
             'branch': '', 'created': x.get('created_at', ''),
             'url': 'https://app.circleci.com/pipelines/workflows/' + workflow_id(x.get('id'))}
            for x in workflows(repo)]


def pipeline_action(repo, value, action):
    value = workflow_id(value)
    if action == 'retry':
        from cost_policy import deny_unverified_external_work
        deny_unverified_external_work('CircleCI workflow retry')
    if action not in ('cancel', 'retry'):
        raise ValueError('Unsupported CircleCI operation.')
    # CLI mutations accept a global UUID, so verify it belongs to the selected
    # project's recent workflow inventory before issuing a mutation.
    if not any(str(x.get('id', '')).lower() == value for x in workflows(repo)):
        raise ValueError('Workflow is not in the selected project recent inventory. Refresh before retrying.')
    argv = ['circleci', 'workflow', 'cancel' if action == 'cancel' else 'rerun', value]
    if action == 'cancel':
        argv.append('--force')  # The dashboard has already required explicit confirmation.
    invoke(argv)
