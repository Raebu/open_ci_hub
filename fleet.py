#!/usr/bin/env python3
"""Opt-in fleet migration. Propose review PRs; never merge or execute job code."""
from __future__ import annotations
import argparse
import base64
import json
import re
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from operations import OWNER, safe_repo, invoke_json
from onboarding import _json_object
from readiness import check as check_readiness

ROOT = Path(__file__).resolve().parent
POLICY = ROOT / '.local' / 'fleet-scopes.json'
RUNS_ON = re.compile(r'(?m)^( {4}runs-on:[ \t]*)(ubuntu-latest|ubuntu-22\.04|ubuntu-24\.04)([ \t]*(?:#.*)?)$')
SHA = re.compile(r'^[a-f0-9]{40}$')
BRANCH = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/-]{0,149}$')
MAX_REPOS = 500
MAX_FILES = 25

@dataclass(frozen=True)
class Change:
    path: str
    sha: str
    content: str
    jobs: int

@dataclass(frozen=True)
class Plan:
    repo: str
    branch: str
    state: str
    explanation: str
    changes: tuple[Change, ...] = ()

    def summary(self):
        return {'repo': self.repo, 'branch': self.branch, 'status': self.state,
                'reason': self.explanation,
                'files': [{'path': x.path, 'jobs': x.jobs} for x in self.changes]}

def validate_owner(owner):
    if not isinstance(owner, str) or not OWNER.fullmatch(owner) or '..' in owner:
        raise ValueError('Specify an exact GitHub account or organisation.')
    return owner

def policy():
    if POLICY.is_symlink() or POLICY.parent.is_symlink():
        raise RuntimeError('Refusing symlinked policy state.')
    if not POLICY.exists():
        return {'github': []}
    try:
        data = json.loads(POLICY.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise RuntimeError('Corrupt fleet policy; refusing changes.') from None
    if not isinstance(data, dict) or not isinstance(data.get('github'), list) or any(
            not isinstance(o, str) or not OWNER.fullmatch(o) for o in data['github']):
        raise RuntimeError('Invalid fleet policy contents.')
    return data

def approved(owner):
    return validate_owner(owner).casefold() in {x.casefold() for x in policy()['github']}

def authorize(owner, confirm=False):
    owner = validate_owner(owner)
    if confirm is not True:
        raise PermissionError('Explicit --confirm required for autonomous review PRs.')
    state = policy()
    if not approved(owner):
        state['github'].append(owner)
    from hub import secure_write
    secure_write(POLICY, json.dumps(state, indent=2) + '\n')
    return state

def discover(owner):
    owner = validate_owner(owner)
    repos = invoke_json(['gh', 'repo', 'list', owner, '--limit', str(MAX_REPOS),
                         '--json', 'nameWithOwner,isArchived,isFork,defaultBranchRef,isPrivate'])
    if not isinstance(repos, list) or len(repos) >= MAX_REPOS:
        raise RuntimeError('Incomplete repository listing or more than 499 repositories.')
    out = []
    for obj in repos:
        if not isinstance(obj, dict) or obj.get('isArchived') or obj.get('isFork'):
            continue
        repo = obj.get('nameWithOwner')
        try:
            safe_repo(repo)
        except ValueError:
            continue
        if len(repo.split('/')) != 2 or repo.split('/')[0].casefold() != owner.casefold():
            continue
        ref = obj.get('defaultBranchRef')
        branch = ref.get('name') if isinstance(ref, dict) else ''
        if not isinstance(branch, str) or not BRANCH.fullmatch(branch) or '..' in branch:
            continue
        out.append((repo, branch))
    return sorted(out, key=lambda x: x[0].casefold())

def event_is_trusted_main(content, branch):
    # This intentionally accepts only unambiguous push-to-default workflows.
    lines = content.splitlines()
    try:
        start = lines.index('on:')
    except ValueError:
        return False
    rest = lines[start+1:]
    end = next((i for i, line in enumerate(rest) if line and not line.startswith((' ', '#'))), len(rest))
    event_lines = rest[:end]
    names = re.findall(r'(?m)^  ([a-z_]+):', '\n'.join(event_lines))
    if 'push' not in names or any(x not in ('push', 'workflow_dispatch') for x in names):
        return False
    try:
        begin = event_lines.index('  push:') + 1
    except ValueError:
        return False
    following = event_lines[begin:]
    stop = next((i for i, line in enumerate(following) if line.startswith('  ') and not line.startswith('    ')), len(following))
    filters = [line.strip().split('#', 1)[0].strip() for line in following[:stop]
               if line.strip() and not line.lstrip().startswith('#')]
    if len(filters) == 1 and filters[0].startswith('branches: [') and filters[0].endswith(']'):
        value = filters[0][len('branches: ['):-1].strip().strip("'\\\"")
        return value == branch
    if len(filters) == 2 and filters[0] == 'branches:' and filters[1].startswith('- '):
        return filters[1][2:].strip().strip("'\\\"") == branch
    return False

def migrate(content, branch):
    if not event_is_trusted_main(content, branch):
        raise RuntimeError('Triggers are not restricted to safe default-branch pushes.')
    if re.search(r'(?m)^ {4}(?:container|services|uses):', content):
        raise RuntimeError('Container/service/reusable jobs require manual toolchain review.')
    if re.search(r'(?m)^\s*-\s*run:\s*(?:sudo|apt-get|docker)\b', content):
        raise RuntimeError('Privileged build steps require manual review.')
    if re.search(r'(?m)^\s*matrix:', content) or 'matrix.' in content:
        raise RuntimeError('Matrix runtime needs a compatible worker review.')
    modified, count = RUNS_ON.subn(lambda m: m.group(1) + '[self-hosted, linux, local-ci]' + m.group(3), content)
    if count and re.search(r'(?m)^ {4}runs-on:\s*(?:ubuntu-|windows-|macos-)', modified):
        raise RuntimeError('Other hosted runner jobs would remain; review the whole workflow together.')
    return modified, count

def _file(repo, branch, path):
    item = _json_object(['gh', 'api', f'repos/{repo}/contents/{path}?ref={quote(branch,safe="")}'])
    if item.get('type') != 'file' or item.get('encoding') != 'base64' or not SHA.fullmatch(str(item.get('sha', ''))):
        raise RuntimeError('Cannot verify a regular workflow file.')
    if not isinstance(item.get('size'), int) or item['size'] > 64000:
        raise RuntimeError('Workflow is larger than the safe migration limit.')
    encoded = item.get('content')
    if not isinstance(encoded, str):
        raise RuntimeError('Workflow text unavailable.')
    try:
        raw = base64.b64decode(encoded)
        if len(raw) != item['size']:
            raise ValueError('Unexpected workflow size')
        return raw.decode('utf-8'), item['sha']
    except (ValueError, UnicodeError):
        raise RuntimeError('Workflow encoding invalid.') from None

def assess(repo, branch):
    repo = safe_repo(repo)
    if len(repo.split('/')) != 2 or not BRANCH.fullmatch(branch):
        raise ValueError('Expected GitHub OWNER/REPO and a valid default branch.')
    tree = _json_object(['gh', 'api', f'repos/{repo}/git/trees/{quote(branch,safe="")}?recursive=1'])
    if tree.get('truncated') is not False or not isinstance(tree.get('tree'), list):
        return Plan(repo, branch, 'manual', 'Repository tree is incomplete.')
    paths = sorted(x['path'] for x in tree['tree'] if isinstance(x, dict)
                   and isinstance(x.get('path'), str) and x['path'].startswith('.github/workflows/')
                   and x['path'].endswith(('.yml', '.yaml')))
    if not paths:
        return Plan(repo, branch, 'no-workflows', 'No workflow: use the existing native onboarding wizard.')
    if len(paths) > MAX_FILES:
        return Plan(repo, branch, 'manual', 'Too many workflows for automatic review.')
    changes = []
    for path in paths:
        try:
            before, sha = _file(repo, branch, path)
            after, jobs = migrate(before, branch)
        except RuntimeError as exc:
            return Plan(repo, branch, 'manual', f'{path}: {exc}')
        if jobs:
            changes.append(Change(path, sha, after, jobs))
    if not changes:
        return Plan(repo, branch, 'already-native', 'No supported hosted jobs were found.')
    return Plan(repo, branch, 'review-ready',
                'Only runner labels are changed; existing build steps are preserved for review.', tuple(changes))

def existing_pr(repo):
    pulls = invoke_json(['gh', 'api', f'repos/{repo}/pulls?state=open&per_page=100'])
    if not isinstance(pulls, list) or len(pulls) >= 100:
        raise RuntimeError('Cannot confirm complete list of existing migration PRs.')
    return any(isinstance(x, dict) and isinstance(x.get('head'), dict) and
               str(x['head'].get('ref', '')).startswith('open-ci-hub/fleet-') for x in pulls)

def create_pr(plan):
    if plan.state != 'review-ready' or not plan.changes:
        raise ValueError('There is no compatible migration candidate.')
    if not approved(plan.repo.split('/')[0]):
        raise PermissionError('Scope not approved. Run fleet.py authorize OWNER --confirm.')
    if existing_pr(plan.repo):
        raise RuntimeError('A fleet migration PR already exists.')
    eligibility = check_readiness('github', plan.repo, plan.branch)
    if not isinstance(eligibility, dict) or eligibility.get('verified') is not True:
        raise RuntimeError('Native worker not verified; no changes made.')
    if assess(plan.repo, plan.branch) != plan:
        raise RuntimeError('Source changed since assessment; re-scan first.')
    ref = _json_object(['gh','api',f'repos/{plan.repo}/git/ref/heads/{quote(plan.branch,safe="")}'])
    sha = (ref.get('object') or {}).get('sha')
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise RuntimeError('Default branch SHA cannot be verified.')
    name = 'open-ci-hub/fleet-' + secrets.token_hex(6)
    _json_object(['gh','api','-X','POST',f'repos/{plan.repo}/git/refs',
                  '-f',f'ref=refs/heads/{name}','-f',f'sha={sha}'])
    for change in plan.changes:
        encoded = base64.b64encode(change.content.encode('utf-8')).decode('ascii')
        _json_object(['gh','api','-X','PUT',f'repos/{plan.repo}/contents/{change.path}',
                      '-f','message=Move trusted main CI to native self-hosted runner',
                      '-f',f'content={encoded}','-f',f'sha={change.sha}','-f',f'branch={name}'])
    result = _json_object(['gh','api','-X','POST',f'repos/{plan.repo}/pulls',
                           '-f','title=Use native Open CI Hub runner for trusted main CI jobs',
                           '-f',f'head={name}','-f',f'base={plan.branch}',
                           '-f','body=Review toolchains, permissions, worker trust and old hosted-job triggers before merging. Only runner routing changes; build steps are preserved. This PR is never auto-merged.'])
    url = result.get('html_url')
    if not isinstance(url, str) or not url.startswith(f'https://github.com/{plan.repo}/pull/'):
        raise RuntimeError('Review branch exists, but PR creation was not confirmed. Inspect GitHub.')
    return url

def sync(owner, apply=False, limit=5):
    if apply and not approved(owner):
        raise PermissionError('Explicit scope authorisation required.')
    if not 1 <= limit <= 5:
        raise ValueError('Between one and five review PRs may be created in one pass.')
    results, opened = [], 0
    for repo, branch in discover(owner):
        try:
            plan = assess(repo, branch)
            data = plan.summary()
            if apply and plan.state == 'review-ready' and opened < limit:
                if existing_pr(repo):
                    data.update(status='pending-review', reason='Already has an open fleet PR.')
                else:
                    data.update(status='review-created', url=create_pr(plan))
                    opened += 1
        except (RuntimeError, ValueError, PermissionError) as exc:
            data = {'repo':repo, 'branch':branch, 'status':'blocked', 'reason':str(exc)}
        results.append(data)
    return results

def main(argv=None):
    parser = argparse.ArgumentParser(description='Discover and prepare native CI migration PRs.')
    sub = parser.add_subparsers(dest='cmd', required=True)
    for action in ('authorize','scan','sync','watch'):
        p = sub.add_parser(action)
        p.add_argument('owner')
        if action == 'authorize':
            p.add_argument('--confirm', action='store_true')
        if action in ('sync','watch'):
            p.add_argument('--apply', action='store_true')
            p.add_argument('--limit', type=int, default=5)
        if action == 'watch':
            p.add_argument('--interval', type=int, default=3600)
    args = parser.parse_args(argv)
    try:
        if args.cmd == 'authorize':
            print(json.dumps(authorize(args.owner, confirm=args.confirm)))
            return 0
        if args.cmd == 'watch':
            if not args.apply or not approved(args.owner) or not 600 <= args.interval <= 86400:
                raise ValueError('Watch requires saved scope, --apply and interval 600-86400 seconds.')
            while True:
                print(json.dumps(sync(args.owner, apply=True, limit=args.limit)), flush=True)
                time.sleep(args.interval)
        print(json.dumps(sync(args.owner, apply=args.cmd == 'sync' and args.apply,
                              limit=getattr(args,'limit',5)), indent=2))
        return 0
    except KeyboardInterrupt:
        return 130
    except (RuntimeError, ValueError, PermissionError, OSError) as exc:
        print('Fleet:', exc, file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
