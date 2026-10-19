"""Shared non-secret CLI helpers for CI provider adapters."""
import json
import re
import subprocess
import shutil
import urllib.parse

RUNNER_TAG = 'local-ci'


def command(args: list[str], *, capture: bool = False, sensitive: bool = False, timeout: int = 120) -> str:
    """Run a trusted installed CLI. Avoid printing token-bearing CLI output/errors."""
    if not shutil.which(args[0]):
        raise RuntimeError(f'{args[0]} is not installed. See docs/INSTALL.md.')
    try:
        result = subprocess.run(args, check=True, text=True, timeout=timeout,
                                stdout=subprocess.PIPE if capture or sensitive else None,
                                stderr=subprocess.PIPE if sensitive else None)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'{args[0]} timed out. Check provider state before retrying.') from None
    except subprocess.CalledProcessError as exc:
        # Provider response bodies may contain secrets or private account information.
        if sensitive:
            raise RuntimeError(f'{args[0]} request failed. Check login, access rights and runner permissions.') from None
        raise RuntimeError(f'{args[0]} exited with status {exc.returncode}.') from None
    return result.stdout if capture or sensitive else ''


def response_object(args: list[str]) -> dict:
    text = command(args, sensitive=True)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError('Provider returned an invalid JSON response.') from None
    if not isinstance(value, dict):
        raise RuntimeError('Expected a JSON object from provider.')
    return value


def secret_from(data: dict, field: str = 'token') -> str:
    token = data.get(field)
    if not isinstance(token, str) or not token or any(x in token for x in '\r\n\x00'):
        raise RuntimeError('Provider did not supply a valid runner token.')
    return token


def input_name(default: str = 'local-ci-runner') -> str:
    return validate_name(input(f'Runner name [{default}]: ').strip() or default)


def validate_name(name: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,60}', name):
        raise ValueError('Runner name must have 1–61 letters, digits, - or _.')
    return name


def https_origin(raw: str) -> str:
    parsed = urllib.parse.urlsplit(raw.strip().rstrip('/'))
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('Enter the HTTPS server origin without paths, ports containing credentials, or query parameters.')
    if parsed.port is not None and parsed.port not in (443, 8443):
        # Explicit port 8443 supports common self-managed instances.
        raise ValueError('Only HTTPS on port 443 or 8443 is supported by this starter.')
    return parsed.geturl().rstrip('/')
