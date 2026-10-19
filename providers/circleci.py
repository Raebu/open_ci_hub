"""CircleCI CLI OAuth sign-in + automatic resource-class token creation."""
from __future__ import annotations
import re
from .common import command, response_object, secret_from, input_name
from . import Registration


def validate_class(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r'[a-z0-9_-]+/[A-Za-z0-9_:+-]+', value):
        raise ValueError('Expected namespace/resource-class.')
    return value


def authenticated(host: str | None = None) -> bool:
    try:
        response_object(['circleci', 'auth', 'me', '--json'])
        return True
    except RuntimeError:
        return False


def login(host: str | None = None) -> None:
    print('Opening CircleCI browser sign-in using the current CircleCI CLI.')
    command(['circleci', 'auth', 'login'])


def prepare() -> Registration:
    name = input_name()
    resource = validate_class(input('CircleCI resource class (namespace/class): '))
    if not authenticated():
        login()
    if input('Create a new resource class? [y/N]: ').strip().lower() == 'y':
        org = input('CircleCI organisation slug (e.g. gh/my-org): ').strip()
        if not re.fullmatch(r'(gh|bb|circleci)/[A-Za-z0-9_.-]+', org):
            raise ValueError('Expected a CircleCI organisation slug such as gh/my-org.')
        response_object(['circleci', 'runner', 'resource-class', 'create', resource,
                         '--org', org, '--json'])
    data = response_object(['circleci', 'runner', 'token', 'create', resource,
                            '--nickname', name, '--json'])
    token = secret_from(data)
    return Registration(name=name, target=resource, token=token,
                        metadata={'resource_class': resource, 'name': name,
                                  'token_id': str(data.get('id', ''))})
