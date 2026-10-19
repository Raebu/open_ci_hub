"""Native CI onboarding: prepare isolated workflow changes and create review requests.

The provider schedules on push; Open CI Hub does not implement an independent
CI scheduler. Never overwrite an existing pipeline during onboarding.
"""
from __future__ import annotations
import base64
import json
import re
import secrets
from dataclasses import dataclass
from urllib.parse import quote
from operations import safe_repo, invoke_json, invoke
from readiness import check as check_readiness

RESOURCE_CLASS = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')
BRANCH = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,149}$')


@dataclass(frozen=True)
class NativePlan:
    provider: str
    repo: str
    path: str
    content: str
    branch: str
    warning: str

    def as_dict(self) -> dict:
        return dict(provider=self.provider, repo=self.repo, path=self.path,
                    content=self.content, branch=self.branch, warning=self.warning)


def _github(branch: str) -> str:
    return f'''# Open CI Hub: native GitHub Actions self-hosted runner workflow.
# Do not execute code from untrusted PRs on a persistent self-hosted PC.
name: Open CI Hub smoke test
on:
  push:
    branches: [{json.dumps(branch)}]
  workflow_dispatch:
permissions:
  contents: read
jobs:
  smoke:
    if: github.event_name == 'push' || github.event_name == 'workflow_dispatch'
    runs-on: [self-hosted, linux, local-ci]
    timeout-minutes: 10
    steps:
      - name: Validate native worker
        run: echo "Open CI Hub runner accepted the job" && uname -s
'''


def _gitlab(branch: str) -> str:
    return f'''# Open CI Hub: GitLab native self-hosted job.
# Runner configured as protected: ensure {branch} is protected.
stages:
  - open-ci-hub-verify
open-ci-hub-smoke:
  stage: open-ci-hub-verify
  tags: [local-ci]
  rules:
    - if: '$CI_COMMIT_BRANCH == "{branch}"'
  script:
    - echo "Open CI Hub runner accepted the job"
    - uname -s
'''


def _circleci(branch: str, resource_class: str) -> str:
    return f'''# Open CI Hub: CircleCI native machine runner resource class.
# CircleCI plan/network/storage limits still apply.
version: 2.1
jobs:
  open-ci-hub-smoke:
    machine: true
    resource_class: {resource_class}
    steps:
      - run:
          name: Validate native worker
          command: echo "Open CI Hub runner accepted the job" && uname -s
workflows:
  native-self-hosted:
    jobs:
      - open-ci-hub-smoke:
          filters:
            branches:
              only: {json.dumps(branch)}
'''


def make_plan(provider: str, repo: str, *, branch: str = 'main', resource_class: str = '') -> NativePlan:
    repo = safe_repo(repo)
    if not isinstance(branch, str) or not BRANCH.fullmatch(branch) or '..' in branch or branch.endswith(('/', '.')) or '//' in branch:
        raise ValueError('Invalid branch name.')
    if provider in ('github', 'circleci') and len(repo.split('/')) != 2:
        raise ValueError('GitHub source repositories require exactly OWNER/REPO.')
    if provider == 'github':
        return NativePlan(provider, repo, '.github/workflows/open-ci-hub.yml', _github(branch), branch,
                          'Adds a separate GitHub Actions smoke workflow. Existing workflows are NOT moved off hosted runners. ' 
                          'Use only a trusted repository or isolated ephemeral worker.')
    if provider == 'gitlab':
        return NativePlan(provider, repo, '.gitlab-ci.yml', _gitlab(branch), branch,
                          'Creates GitLab CI only if no .gitlab-ci.yml exists. Existing pipelines require a reviewed merge. ' 
                          'The target branch must be protected for this runner.')
    if provider == 'circleci':
        if not isinstance(resource_class, str) or not RESOURCE_CLASS.fullmatch(resource_class) or '..' in resource_class:
            raise ValueError('CircleCI requires your authorised namespace/resource-class.')
        return NativePlan(provider, repo, '.circleci/config.yml', _circleci(branch, resource_class), branch,
                          'CircleCI uses a GitHub repository as source in this initial onboarding adapter. ' 
                          'Existing .circleci/config.yml will NOT be overwritten. Confirm CircleCI project follows this repository.')
    raise ValueError('Unsupported native provider.')


def _json_object(args: list[str]) -> dict:
    data = invoke_json(args)
    if not isinstance(data, dict):
        raise RuntimeError('Provider returned an unexpected object.')
    return data


def _get_github_branch_and_tree(repo: str) -> tuple[str, set[str]]:
    meta = _json_object(['gh', 'api', f'repos/{repo}'])
    default = meta.get('default_branch')
    if not isinstance(default, str) or not BRANCH.fullmatch(default):
        raise RuntimeError('Cannot verify GitHub default branch.')
    tree = _json_object(['gh', 'api', f'repos/{repo}/git/trees/{quote(default, safe="")}?recursive=1'])
    if tree.get('truncated') is True or not isinstance(tree.get('tree'), list):
        raise RuntimeError('Cannot verify the full GitHub repository tree; refusing to overwrite files.')
    return default, {item['path'] for item in tree['tree'] if isinstance(item,dict) and isinstance(item.get('path'),str)}


def _get_gitlab_branch_and_tree(repo: str) -> tuple[str, set[str]]:
    encoded = quote(repo, safe='')
    meta = _json_object(['glab', 'api', f'projects/{encoded}'])
    default = meta.get('default_branch')
    if not isinstance(default, str) or not BRANCH.fullmatch(default):
        raise RuntimeError('Cannot verify GitLab default branch.')
    # Avoid relying on incomplete/paginated tree listing. The commit API's
    # `action=create` atomically fails if the file already exists.
    return default, set()


def _create_github_pr(plan: NativePlan) -> dict:
    default, paths = _get_github_branch_and_tree(plan.repo)
    if plan.path in paths:
        raise RuntimeError('A workflow already exists at the target path. Do not overwrite it; review/migrate manually.')
    if plan.branch != default:
        raise ValueError(f'Onboarding branch {plan.branch!r} does not match the repository default branch {default!r}. Preview the correct target before applying.')
    name = 'open-ci-hub/onboard-' + secrets.token_hex(5)
    repo = plan.repo
    ref = _json_object(['gh','api',f'repos/{repo}/git/ref/heads/{quote(default, safe="")}'])
    sha = ref.get('object',{}).get('sha') if isinstance(ref.get('object'),dict) else None
    if not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{40}',sha):
        raise RuntimeError('Unable to verify default branch commit; no changes made.')
    _json_object(['gh','api','-X','POST',f'repos/{repo}/git/refs','-f',f'ref=refs/heads/{name}','-f',f'sha={sha}'])
    # The Contents API requires base64. Keep it within a native provider API
    # request, rather than passing through a shell or committing local secrets.
    encoded = base64.b64encode(plan.content.encode()).decode('ascii')
    _json_object(['gh','api','-X','PUT',f'repos/{repo}/contents/{plan.path}',
                  '-f',f'message=Add native Open CI Hub push workflow',
                  '-f',f'content={encoded}', '-f',f'branch={name}'])
    pr = _json_object(['gh','api','-X','POST',f'repos/{repo}/pulls',
                      '-f','title=Add native self-hosted CI with Open CI Hub',
                      '-f',f'head={name}', '-f',f'base={default}',
                      '-f','body=Review self-hosted runner trust, labels, branch protections and resource limits before merging. Existing CI workflows remain unchanged.'])
    url = pr.get('html_url')
    if not isinstance(url,str) or not url.startswith(f'https://github.com/{repo}/pull/'):
        raise RuntimeError('Branch and workflow created, but pull request confirmation was not returned; check GitHub before retrying.')
    return dict(url=url, branch=name, message='Review pull request and merge to activate push-based CI.')


def _create_gitlab_mr(plan: NativePlan) -> dict:
    default, _ = _get_gitlab_branch_and_tree(plan.repo)
    if plan.branch != default:
        raise ValueError(f'Onboarding branch {plan.branch!r} does not match the repository default branch {default!r}. Preview the correct target before applying.')
    name = 'open-ci-hub/onboard-' + secrets.token_hex(5)
    encoded = quote(plan.repo, safe='')
    _json_object(['glab','api','-X','POST',f'projects/{encoded}/repository/branches',
                  '-f',f'branch={name}', '-f',f'ref={default}'])
    file_path = quote(plan.path, safe='')
    # GitLab create endpoint rejects an existing .gitlab-ci.yml atomically.
    _json_object(['glab','api','-X','POST',f'projects/{encoded}/repository/files/{file_path}',
                  '-f',f'branch={name}','-f','commit_message=Add native Open CI Hub push pipeline',
                  '-f',f'content={plan.content}'])
    mr = _json_object(['glab','api','-X','POST',f'projects/{encoded}/merge_requests',
                       '-f',f'source_branch={name}', '-f',f'target_branch={default}',
                       '-f','title=Add native self-hosted CI with Open CI Hub',
                       '-f','description=Confirm the default branch is protected, review runner security and merge to activate. Existing files will never be overwritten.'])
    url = mr.get('web_url')
    if not isinstance(url,str) or not url.startswith('https://'):
        raise RuntimeError('GitLab change created but merge request confirmation missing; inspect provider before retrying.')
    return dict(url=url, branch=name, message='Review merge request and merge to activate push-based CI on the protected branch.')


def create_review_request(plan: NativePlan, *, approved: bool = False) -> dict:
    if not approved:
        raise PermissionError('Approval is required to create a branch and open a review request.')
    readiness = check_readiness(plan.provider, plan.repo, plan.branch)
    if not isinstance(readiness, dict) or readiness.get('verified') is not True:
        raise RuntimeError('Native runner readiness is unverified. No repository changes were made. '
                           'Register and validate an eligible native worker before activating CI.')
    if plan.provider in ('github','circleci'):
        return _create_github_pr(plan)
    if plan.provider == 'gitlab':
        return _create_gitlab_mr(plan)
    raise ValueError('Unsupported provider.')
