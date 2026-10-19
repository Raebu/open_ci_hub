"""Read-only native runner eligibility checks. Container uptime is not readiness."""
from urllib.parse import quote
from operations import invoke_json, safe_repo


def _object(args):
    data = invoke_json(args)
    if not isinstance(data, dict):
        raise RuntimeError('Provider returned an invalid readiness response.')
    return data


def check(provider: str, repo: str, branch: str, resource_class: str = '') -> dict:
    repo = safe_repo(repo)
    if provider in ('github', 'circleci') and len(repo.split('/')) != 2:
        raise ValueError('GitHub source repositories require exactly OWNER/REPO.')
    if provider == 'github':
        meta = _object(['gh', 'api', f'repos/{repo}'])
        if not isinstance(meta.get('permissions'), dict) or meta['permissions'].get('push') is not True:
            raise RuntimeError('Repository write permission is required for onboarding.')
        policy = _object(['gh', 'api', f'repos/{repo}/actions/permissions'])
        if policy.get('enabled') is not True:
            raise RuntimeError('GitHub Actions is disabled for this repository.')
        data = _object(['gh', 'api', f'repos/{repo}/actions/runners?per_page=100'])
        runners = data.get('runners')
        if not isinstance(runners, list):
            raise RuntimeError('Cannot verify repository runner eligibility.')
        eligible = []
        for runner in runners:
            if not isinstance(runner, dict):
                continue
            labels = runner.get('labels', [])
            if not isinstance(labels, list):
                continue
            labels = {str(x.get('name', '')).lower() for x in labels if isinstance(x, dict)}
            if runner.get('status') == 'online' and {'self-hosted', 'linux', 'local-ci'} <= labels:
                eligible.append({'name': runner.get('name', ''), 'busy': runner.get('busy') is True})
        if not eligible:
            raise RuntimeError('No online eligible GitHub runner found in the first 100 runners. Register/start a repository-accessible Linux runner with the local-ci label, then retry.')
    elif provider == 'gitlab':
        project = quote(repo, safe='')
        ref = _object(['glab', 'api', f'projects/{project}/repository/branches/{quote(branch, safe="")}'])
        if ref.get('protected') is not True:
            raise RuntimeError('Protect the GitLab target branch before onboarding a protected runner.')
        runners = []
        for kind in ('project_type', 'group_type'):
            page = invoke_json(['glab', 'api', f'projects/{project}/runners?type={kind}&per_page=100'])
            if not isinstance(page, list):
                raise RuntimeError('Cannot verify project runner eligibility.')
            runners.extend(page)
        eligible = []
        for runner in runners:
            if not isinstance(runner, dict) or runner.get('status') != 'online' or runner.get('paused') is not False:
                continue
            if runner.get('runner_type') not in ('project_type', 'group_type'):
                continue  # Never consider shared/instance compute eligible.
            rid = runner.get('id')
            if not isinstance(rid, int) or isinstance(rid, bool) or rid <= 0:
                continue
            detail = _object(['glab', 'api', f'runners/{rid}'])
            tags = detail.get('tag_list')
            if (isinstance(tags, list) and 'local-ci' in tags and detail.get('access_level') == 'ref_protected'
                    and detail.get('paused') is False and detail.get('status') == 'online'):
                eligible.append({'name': runner.get('description', ''), 'busy': None})
        if not eligible:
            raise RuntimeError('No online protected project/group runner with local-ci tag found in the first 100 runners per scope. Register/start your worker and check project access.')
    elif provider == 'circleci':
        from onboarding import RESOURCE_CLASS
        if not isinstance(resource_class, str) or not RESOURCE_CLASS.fullmatch(resource_class):
            raise ValueError('CircleCI requires a namespace/resource-class name.')
        org = 'gh/' + repo.split('/')[0]
        project_slug = 'gh/' + repo
        project = _object(['circleci', 'project', 'get', '--project', project_slug, '--json'])
        if str(project.get('slug', '')).lower() != project_slug.lower():
            raise RuntimeError('The CircleCI project has not been verified for this GitHub repository.')
        if project.get('vcs_default_branch') != branch:
            raise RuntimeError('The CircleCI default branch does not match this onboarding target.')
        settings = _object(['circleci', 'project', 'setting', 'list', '--project', project_slug, '--json'])
        if settings.get('enable_building_fork_prs') is not False:
            raise RuntimeError('Disable builds from forked pull requests before using a personal self-hosted runner.')
        if settings.get('is_running_disabled') is not False:
            raise RuntimeError('CircleCI project execution is disabled or cannot be verified.')
        classes = invoke_json(['circleci', 'runner', 'resource-class', 'list',
                               '--resource-class', resource_class, '--org', org, '--json'])
        if not isinstance(classes, list) or not any(
                isinstance(item, dict) and item.get('resource_class') == resource_class for item in classes):
            raise RuntimeError('The CircleCI resource class is not accessible from the repository organisation.')
        instances = invoke_json(['circleci', 'runner', 'instance', 'list',
                                 '--resource-class', resource_class, '--org', org, '--json'])
        if not isinstance(instances, list):
            raise RuntimeError('CircleCI returned an invalid runner instance inventory.')
        eligible = [
            {'name': item.get('name', ''), 'busy': item.get('is_busy') is True}
            for item in instances
            if isinstance(item, dict)
            and item.get('resource_class') == resource_class
            and item.get('status') == 'online'
        ]
        if not eligible:
            raise RuntimeError('No online CircleCI instance for this resource class. Start an approved runner and retry.')
    else:
        raise ValueError('Unsupported provider.')
    return {'verified': True, 'runners': eligible,
            'message': 'Native provider reports an eligible online worker. Availability can change; verify workflow policies and the first push build.'}
