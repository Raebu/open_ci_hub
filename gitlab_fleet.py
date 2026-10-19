#!/usr/bin/env python3
"""GitLab group CI migration: opt-in, protected, review-only, native runner tags."""
from __future__ import annotations
import argparse
import base64
from dataclasses import dataclass
import json
from pathlib import Path
import re
import secrets
import sys
import time
from urllib.parse import quote
from operations import OWNER, safe_repo, invoke_json
from onboarding import _json_object
from readiness import check as check_readiness
from hub import secure_write

ROOT = Path(__file__).resolve().parent
POLICY = ROOT / '.local' / 'gitlab-fleet-scopes.json'
SEGMENT = re.compile(r'^[A-Za-z0-9_.-]{1,100}$')
SHA = re.compile(r'^[a-f0-9]{40}$')
SAFE_JOB = re.compile(r'(?m)^([A-Za-z][A-Za-z0-9_-]{0,99}):[ \t]*$')
RESERVED = {
    'workflow', 'stages', 'variables', 'default', 'include', 'image',
    'services', 'cache', 'before_script', 'after_script', 'spec',
}
MAX_PROJECTS = 500

@dataclass(frozen=True)
class Plan:
    project: str
    branch: str
    state: str
    reason: str
    content: str = ''
    last_commit_id: str = ''
    jobs: int = 0

    def summary(self):
        return {'repo': self.project, 'branch': self.branch,
                'status': self.state, 'reason': self.reason, 'jobs': self.jobs}

def group_path(value):
    if not isinstance(value, str) or len(value) > 300 or any(
            not SEGMENT.fullmatch(x) or '..' in x for x in value.split('/')):
        raise ValueError('Choose an exact GitLab group or subgroup path.')
    return value

def policy():
    if POLICY.is_symlink() or POLICY.parent.is_symlink():
        raise RuntimeError('Refusing symlinked group policy.')
    if not POLICY.exists():
        return {'groups': [], 'users': []}
    try:
        data = json.loads(POLICY.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise RuntimeError('Corrupt GitLab group policy.') from None
    if not isinstance(data, dict) or not isinstance(data.get('groups'), list) or any(
            not isinstance(g, str) or len(g) > 300 or not all(SEGMENT.fullmatch(s) and '..' not in s for s in g.split('/'))
            for g in data['groups']):
        raise RuntimeError('Invalid GitLab group policy.')
    if (not isinstance(data.get('users', []), list) or
            any(not isinstance(u, str) or not SEGMENT.fullmatch(u) or '..' in u
                for u in data.get('users', []))):
        raise RuntimeError('Invalid GitLab personal-namespace policy.')
    return data

def approved(group):
    return group_path(group).casefold() in {x.casefold() for x in policy()['groups']}

def authorize(group, confirm=False):
    group = group_path(group)
    if confirm is not True:
        raise PermissionError('Explicit --confirm required.')
    settings = policy()
    if not approved(group):
        settings['groups'].append(group)
    secure_write(POLICY, json.dumps(settings, indent=2) + '\n')
    return settings

def personal_approved(username):
    username = group_path(username)
    if '/' in username:
        raise ValueError('Personal namespace must be a single GitLab username.')
    return username.casefold() in {x.casefold() for x in policy().get('users', [])}


def _verify_current_user(username):
    username = group_path(username)
    if '/' in username:
        raise ValueError('Personal namespace must be a single GitLab username.')
    identity = _json_object(['glab', 'api', 'user'])
    if (not isinstance(identity.get('username'), str) or
            identity['username'].casefold() != username.casefold() or
            not isinstance(identity.get('id'), int) or isinstance(identity.get('id'), bool)):
        raise PermissionError('Sign in to glab as this exact personal GitLab account.')
    return identity


def authorize_personal(username, confirm=False):
    _verify_current_user(username)
    if confirm is not True:
        raise PermissionError('Explicit --confirm required for personal GitLab review MRs.')
    settings = policy()
    users = settings.setdefault('users', [])
    if not personal_approved(username):
        users.append(username)
    secure_write(POLICY, json.dumps(settings, indent=2) + '\n')
    return settings


def discover_personal(username):
    """List signed-in user's owned projects, excluding groups and shared projects."""
    _verify_current_user(username)
    seen = {}
    for page in range(1, 11):
        # Owned projects endpoint works for authenticated private-profile users.
        items = invoke_json(['glab', 'api',
                             f'projects?owned=true&simple=true&per_page=50&page={page}'])
        if not isinstance(items, list):
            raise RuntimeError('Unexpected personal GitLab project inventory.')
        for item in items:
            if (not isinstance(item, dict) or item.get('archived') or
                    item.get('forked_from_project')):
                continue
            project, branch = item.get('path_with_namespace'), item.get('default_branch')
            try:
                safe_repo(project)
            except ValueError:
                continue
            if (len(project.split('/')) == 2 and
                    project.split('/')[0].casefold() == username.casefold() and
                    isinstance(branch, str) and
                    re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,149}', branch) and
                    '..' not in branch):
                seen[project] = branch
        if len(items) < 50:
            return sorted(seen.items(), key=lambda x: x[0].casefold())
    raise RuntimeError('Personal GitLab project pagination limit reached; refusing a partial scan.')


def discover(group):
    group = group_path(group)
    seen = {}
    encoded = quote(group, safe='')
    for page in range(1, 11):
        items = invoke_json(['glab', 'api',
                             f'groups/{encoded}/projects?include_subgroups=true&with_shared=false&simple=true&per_page=50&page={page}'])
        if not isinstance(items, list):
            raise RuntimeError('Unexpected GitLab group project inventory.')
        for item in items:
            if not isinstance(item, dict) or item.get('archived') or item.get('forked_from_project'):
                continue
            path, branch = item.get('path_with_namespace'), item.get('default_branch')
            try:
                safe_repo(path)
            except ValueError:
                continue
            if (path.casefold().startswith(group.casefold() + '/')
                    and isinstance(branch, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,149}', branch)
                    and '..' not in branch):
                seen[path] = branch
        if len(items) < 50:
            return sorted(seen.items(), key=lambda x: x[0].casefold())
    raise RuntimeError('GitLab project pagination limit reached; no automatic partial application.')

def _root_has_ci(project, branch):
    """Confirm absence using an accessible complete root tree; never infer from API failure."""
    encoded = quote(project, safe='')
    total = 0
    for page in range(1, 11):
        rows = invoke_json(['glab', 'api', f'projects/{encoded}/repository/tree?'
                            f'ref={quote(branch,safe="")}&per_page=100&page={page}'])
        if not isinstance(rows, list):
            raise RuntimeError('Cannot inspect complete GitLab project root tree.')
        total += len(rows)
        if any(isinstance(row, dict) and row.get('path') == '.gitlab-ci.yml'
               for row in rows):
            return True
        if len(rows) < 100:
            return False
    raise RuntimeError('Project root tree exceeds safe pagination limit; cannot confirm missing CI.')


def _get_config(project, branch):
    encoded = quote(project, safe='')
    path = quote('.gitlab-ci.yml', safe='')
    try:
        file = _json_object(['glab', 'api',
                             f'projects/{encoded}/repository/files/{path}?ref={quote(branch, safe="")}'])
    except RuntimeError:
        return None  # Missing/forbidden must still fail safely before writing.
    if file.get('encoding') != 'base64' or not isinstance(file.get('content'), str):
        raise RuntimeError('Cannot read GitLab CI YAML for safe modification.')
    commit = file.get('last_commit_id')
    if not isinstance(commit, str) or not SHA.fullmatch(commit):
        raise RuntimeError('GitLab CI last commit ID unavailable.')
    try:
        data = base64.b64decode(file['content'])
        if len(data) > 64000:
            raise ValueError('YAML too large')
        return data.decode('utf-8'), commit
    except (ValueError, UnicodeError):
        raise RuntimeError('Cannot decode GitLab CI configuration.') from None

def migrate(content):
    # A verified global workflow rule must block merge-request and other refs
    # on a persistent, protected shell worker. Refuse ambiguous/complex YAML.
    headers = list(SAFE_JOB.finditer(content))
    workflow = next((i for i, h in enumerate(headers) if h.group(1) == 'workflow'), None)
    if workflow is None:
        raise RuntimeError('A global default-branch-only workflow is required.')
    start = headers[workflow].start()
    end = headers[workflow+1].start() if workflow+1 < len(headers) else len(content)
    lines = [line.strip() for line in content[start:end].splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    allowed = ["workflow:", "rules:",
               "- if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'"]
    if lines != allowed:
        raise RuntimeError('Workflow must contain exactly one default-branch-only rule.')
    if re.search(r'(?m)^(include|image|services|default|before_script):', content):
        raise RuntimeError('Inherited includes/images/default config require manual runner review.')
    if re.search(r'(?m)^  (?:trigger|extends|parallel|image|services|tags):', content):
        raise RuntimeError('Complex pipeline requires manual review.')
    jobs = [m for m in headers if m.group(1) not in RESERVED]
    if not jobs:
        raise RuntimeError('No simple script jobs to migrate.')
    patches = []
    for header in jobs:
        i = headers.index(header)
        end = headers[i + 1].start() if i + 1 < len(headers) else len(content)
        body = content[header.end():end]
        if not re.search(r'(?m)^  script:', body):
            raise RuntimeError(f'Job {header.group(1)} does not contain a simple script.')
        if re.search(r'(?m)^  (?:tags|trigger|extends|parallel|image|services):', body):
            raise RuntimeError(f'Job {header.group(1)} has runner or executor customisation.')
        patches.append(header.end())
    result = content
    for pos in reversed(patches):
        result = result[:pos] + '\n  tags: [local-ci]' + result[pos:]
    return result, len(patches)

def assess(project, branch):
    project = safe_repo(project)
    try:
        read = _get_config(project, branch)
        if read is None:
            try:
                present = _root_has_ci(project, branch)
            except RuntimeError as exc:
                return Plan(project, branch, 'blocked',
                            'GitLab CI could not be read and repository root could not be verified: ' + str(exc))
            if present:
                return Plan(project, branch, 'blocked',
                            'Existing GitLab CI file is inaccessible; refusing to overwrite it.')
            from onboarding import make_plan
            setup = make_plan('gitlab', project, branch=branch)
            return Plan(project, branch, 'onboard-ready',
                        'No existing GitLab CI; propose a create-only native runner smoke pipeline. '
                        'Actual project tests/builds must be added separately after review.',
                        setup.content, '', 1)
        source, commit = read
        changed, jobs = migrate(source)
        return Plan(project, branch, 'review-ready',
                    'Adds native local-ci tags only; existing scripts preserved for review.',
                    changed, commit, jobs)
    except RuntimeError as exc:
        return Plan(project, branch, 'manual', str(exc))

def existing_mr(project):
    encoded = quote(project, safe='')
    items = invoke_json(['glab','api',f'projects/{encoded}/merge_requests?state=opened&per_page=100'])
    if not isinstance(items,list) or len(items) >= 100:
        raise RuntimeError('Cannot confirm all open GitLab merge requests.')
    return any(isinstance(x, dict) and str(x.get('source_branch', '')).startswith(
               ('open-ci-hub/fleet-', 'open-ci-hub/onboard-')) for x in items)

def create_mr(plan):
    if plan.state not in ('review-ready', 'onboard-ready') or not plan.jobs:
        raise ValueError('No safe migration/onboarding candidate.')
    settings = policy()
    group_allowed = any(plan.project.casefold().startswith(g.casefold() + '/')
                        for g in settings['groups'])
    personal_allowed = (len(plan.project.split('/')) == 2 and
                        plan.project.split('/')[0].casefold() in
                        {u.casefold() for u in settings.get('users', [])})
    if not (group_allowed or personal_allowed):
        raise PermissionError('GitLab group or personal namespace not authorised.')
    if personal_allowed and not group_allowed:
        _verify_current_user(plan.project.split('/')[0])
    if existing_mr(plan.project):
        raise RuntimeError('A GitLab fleet MR already exists.')
    eligibility = check_readiness('gitlab', plan.project, plan.branch)
    if not isinstance(eligibility, dict) or eligibility.get('verified') is not True:
        raise RuntimeError('Protected native GitLab runner is unavailable.')
    if assess(plan.project, plan.branch) != plan:
        raise RuntimeError('GitLab CI config changed; re-run assessment.')
    project_id = quote(plan.project, safe='')
    meta = _json_object(['glab','api',f'projects/{project_id}'])
    if meta.get('default_branch') != plan.branch:
        raise RuntimeError('GitLab default branch changed; refusing migration.')
    if plan.state == 'onboard-ready':
        from onboarding import make_plan, _create_gitlab_mr
        template = make_plan('gitlab', plan.project, branch=plan.branch)
        if plan.content != template.content or plan.last_commit_id:
            raise RuntimeError('Unrecognised onboarding template; refusing file creation.')
        # The existing native onboarding helper uses the GitLab create-only
        # file endpoint: it must fail rather than replace any existing CI.
        return _create_gitlab_mr(template)['url']
    branch = 'open-ci-hub/fleet-' + secrets.token_hex(6)
    _json_object(['glab','api','-X','POST',f'projects/{project_id}/repository/branches',
                  '-f',f'branch={branch}','-f',f'ref={plan.branch}'])
    _json_object(['glab','api','-X','PUT',f'projects/{project_id}/repository/files/{quote(".gitlab-ci.yml",safe="")}',
                  '-f',f'branch={branch}', '-f',f'last_commit_id={plan.last_commit_id}',
                  '-f','commit_message=Move trusted GitLab CI to protected local-ci runner',
                  '-f',f'content={plan.content}'])
    result = _json_object(['glab','api','-X','POST',f'projects/{project_id}/merge_requests',
                           '-f',f'source_branch={branch}','-f',f'target_branch={plan.branch}',
                           '-f','title=Route protected GitLab CI jobs to Open CI Hub runners',
                           '-f','description=Review runner trust, tools, scripts and environment compatibility before merge. All script bodies are preserved. This MR is not merged automatically.'])
    url = result.get('web_url')
    if not isinstance(url,str) or not url.startswith('https://'):
        raise RuntimeError('GitLab review branch created but merge request unconfirmed.')
    return url

def sync(group, apply=False, limit=5, *, personal=False):
    if apply and not (personal_approved(group) if personal else approved(group)):
        raise PermissionError('Approve exact GitLab namespace with --confirm first.')
    if not isinstance(limit,int) or not 1 <= limit <= 5:
        raise ValueError('Maximum 5 MRs per pass.')
    output, opened = [], 0
    projects = discover_personal(group) if personal else discover(group)
    for project, branch in projects:
        try:
            plan = assess(project, branch)
            item = plan.summary()
            if apply and plan.state in ('review-ready', 'onboard-ready') and opened < limit:
                if existing_mr(project):
                    item.update(status='pending-review', reason='Existing fleet MR awaiting review.')
                else:
                    item.update(status='review-created', url=create_mr(plan))
                    opened += 1
        except (RuntimeError,ValueError,PermissionError) as exc:
            item = {'repo':project,'branch':branch,'status':'blocked','reason':str(exc)}
        output.append(item)
    return output

def main(argv=None):
    parser = argparse.ArgumentParser(description='Opt-in GitLab group CI migration.')
    sub = parser.add_subparsers(dest='command',required=True)
    for cmd in ('authorize','scan','sync','watch',
                'authorize-personal','scan-personal','sync-personal','watch-personal'):
        item = sub.add_parser(cmd)
        item.add_argument('group')
        if cmd.startswith('authorize'):
            item.add_argument('--confirm',action='store_true')
        if cmd.startswith(('sync','watch')):
            item.add_argument('--apply',action='store_true')
            item.add_argument('--limit',type=int,default=5)
        if cmd.startswith('watch'):
            item.add_argument('--interval',type=int,default=3600)
    args=parser.parse_args(argv)
    try:
        personal = args.command.endswith('-personal')
        action = args.command.removesuffix('-personal')
        if action == 'authorize':
            result = (authorize_personal(args.group, confirm=args.confirm) if personal
                      else authorize(args.group, confirm=args.confirm))
            print(json.dumps(result))
            return 0
        if action == 'watch':
            valid = personal_approved(args.group) if personal else approved(args.group)
            if not args.apply or not valid or not 600 <= args.interval <= 86400:
                raise ValueError('Watch requires approved scope, --apply, and 600-86400s interval.')
            while True:
                print(json.dumps(sync(args.group,apply=True,limit=args.limit,personal=personal)),flush=True)
                time.sleep(args.interval)
        print(json.dumps(sync(args.group, apply=action=='sync' and args.apply,
                              limit=getattr(args,'limit',5),personal=personal),indent=2))
        return 0
    except KeyboardInterrupt:
        return 130
    except (RuntimeError,ValueError,PermissionError,OSError) as exc:
        print('GitLab fleet:',exc,file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
