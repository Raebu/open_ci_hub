"""GitLab browser sign-in + project runner creation via recommended API."""
from __future__ import annotations
import re
from urllib.parse import quote, urlsplit
from .common import command, response_object, secret_from, input_name, RUNNER_TAG
from . import Registration


def validate_target(value: str) -> tuple[str, str, str]:
    value = value.strip().rstrip('/')
    uri = urlsplit(value)
    if uri.scheme != 'https' or not uri.hostname or uri.query or uri.fragment or uri.username or uri.password:
        raise ValueError('Enter a full HTTPS GitLab project URL.')
    if uri.port is not None and uri.port not in (443, 8443):
        raise ValueError('Unsupported HTTPS port.')
    project = uri.path.lstrip('/')
    if len(project.split('/')) < 2 or not all(re.fullmatch(r'[A-Za-z0-9_.-]+', p) for p in project.split('/')):
        raise ValueError('Enter https://gitlab.com/GROUP/PROJECT (subgroups supported).')
    origin = f'https://{uri.netloc}'
    return value, origin, project


def authenticated(host: str | None = None) -> bool:
    host = host or 'gitlab.com'
    try:
        command(['glab', 'auth', 'status', '--hostname', host], sensitive=True)
        return True
    except RuntimeError:
        return False


def login(host: str | None = None) -> None:
    host = host or 'gitlab.com'
    print(f'Opening GitLab browser sign-in for {host}.')
    if host != 'gitlab.com':
        print('Self-managed GitLab OAuth may require the server owner to create an OAuth application (client ID).')
    command(['glab', 'auth', 'login', '--hostname', host, '--web'], timeout=600)


def prepare() -> Registration:
    target, origin, path = validate_target(input('GitLab project URL: '))
    host = urlsplit(origin).netloc
    name = input_name()
    if not authenticated(host):
        login(host)
    encoded = quote(path, safe='')
    project = response_object(['glab', 'api', f'projects/{encoded}', '--hostname', host])
    project_id = project.get('id')
    if not isinstance(project_id, int) or isinstance(project_id, bool):
        raise RuntimeError('Could not resolve GitLab project ID. Check project access.')
    # Runner restrictions are established server-side before receiving the secret.
    created = response_object(['glab', 'api', '--hostname', host, '-X', 'POST', 'user/runners',
                               '-f', 'runner_type=project_type', '-F', f'project_id={project_id}',
                               '-f', f'description={name}', '-f', f'tag_list={RUNNER_TAG}',
                               '-F', 'run_untagged=false', '-f', 'access_level=ref_protected'])
    token = secret_from(created)
    return Registration(name=name, target=origin, token=token,
                        metadata={'url': origin, 'project': target, 'name': name,
                                  'runner_id': str(created.get('id', ''))})

def prepare_group() -> Registration:
    """Create a protected group-level runner for multiple authorised projects."""
    target = input('GitLab group URL: ').strip().rstrip('/')
    uri = urlsplit(target)
    group = uri.path.strip('/')
    if uri.scheme != 'https' or not uri.hostname or uri.username or uri.password or uri.query or uri.fragment:
        raise ValueError('Enter a full HTTPS GitLab group URL.')
    if uri.port is not None and uri.port not in (443, 8443):
        raise ValueError('Unsupported GitLab HTTPS port.')
    if not group or not all(re.fullmatch(r'[A-Za-z0-9_.-]+', segment) for segment in group.split('/')):
        raise ValueError('Enter a valid GitLab group path (subgroups supported).')
    host = uri.netloc
    origin = f'https://{uri.netloc}'
    name = input_name()
    if not authenticated(host):
        login(host)
    details = response_object(['glab', 'api', f'groups/{quote(group, safe="")}', '--hostname', host])
    group_id = details.get('id')
    if not isinstance(group_id,int) or isinstance(group_id,bool):
        raise RuntimeError('Could not resolve GitLab group ID. Check group access.')
    created = response_object(['glab', 'api', '--hostname', host, '-X', 'POST', 'user/runners',
                               '-f', 'runner_type=group_type', '-F', f'group_id={group_id}',
                               '-f', f'description={name}', '-f', f'tag_list={RUNNER_TAG}',
                               '-F', 'run_untagged=false', '-f', 'access_level=ref_protected'])
    return Registration(name=name, target=origin, token=secret_from(created),
                        metadata={'url': origin, 'group': target, 'scope': 'group',
                                  'name': name, 'runner_id': str(created.get('id',''))})
