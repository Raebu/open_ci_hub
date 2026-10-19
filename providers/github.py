"""GitHub CLI OAuth sign-in + repository-scoped runner token automation."""
from __future__ import annotations
import re
from urllib.parse import urlsplit
from .common import command, response_object, secret_from, input_name, validate_name
from . import Registration


def validate_target(value: str) -> tuple[str, str]:
    value = value.strip().rstrip('/')
    uri = urlsplit(value)
    if uri.scheme != 'https' or uri.netloc != 'github.com' or uri.query or uri.fragment or uri.username or uri.password:
        raise ValueError('Enter https://github.com/OWNER/REPO or https://github.com/ORG')
    parts = uri.path.lstrip('/').split('/')
    if len(parts) not in (1, 2) or any(not re.fullmatch(r'[\w.-]+', p) for p in parts):
        raise ValueError('Invalid GitHub owner/repository address.')
    if len(parts) == 1:
        return value, f'orgs/{parts[0]}/actions/runners/registration-token'
    return value, f'repos/{parts[0]}/{parts[1]}/actions/runners/registration-token'


def authenticated(host: str | None = None) -> bool:
    try:
        command(['gh', 'auth', 'status', '--hostname', 'github.com'], sensitive=True)
        return True
    except RuntimeError:
        return False


def login(host: str | None = None) -> None:
    print('Opening GitHub browser sign-in. Grant repository access required to administer runners.')
    command(['gh', 'auth', 'login', '--hostname', 'github.com', '--web', '--scopes', 'repo'], timeout=600)


def prepare_target(value: str, name: str) -> Registration:
    target, endpoint = validate_target(value)
    name = validate_name(name)
    if not authenticated():
        raise RuntimeError('Sign in with `gh auth login --web --scopes repo` before noninteractive setup.')
    try:
        token = secret_from(response_object(['gh', 'api', '--method', 'POST', f'/{endpoint}']))
    except RuntimeError:
        if endpoint.startswith('orgs/'):
            raise RuntimeError('Organisation runner access requires organisation administration and may require `gh auth refresh -s admin:org`.') from None
        raise RuntimeError('GitHub runner creation failed. Repository admin access and repo scope are required.') from None
    return Registration(name=name, target=target, token=token,
                        metadata={'url': target, 'name': name})


def prepare() -> Registration:
    target = input('GitHub repository URL (recommended) or organisation URL: ')
    validate_target(target)
    name = input_name()
    if not authenticated():
        login()
    return prepare_target(target, name)
