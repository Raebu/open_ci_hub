# Contributing to Open CI Hub

Thanks for helping make self-hosted CI approachable and provider-independent.

Contributions are welcome under the MIT licence. Open an issue describing the improvement before large or security-sensitive changes. Do not include account credentials, real runner tokens, private repository contents, or CI secrets in issues or pull requests.

## Development

```bash
python3 -m unittest discover -s tests -v
python3 hub.py doctor
```

Do not require paid GitHub Actions or GitLab hosted runners for ordinary changes; local tests are designed to run with the Python standard library. Testing Docker integrations or real account registrations must happen in an explicitly authorised test environment.

## Add another provider

1. Add a small adapter in `providers/` with `login(host=None)`, `authenticated(host=None)` and `prepare()` returning a `Registration` with secret only in memory.
2. Register that adapter in `providers/__init__.py`.
3. Add a dedicated runner Compose service and configure `hub.register` / `hub.CONTAINERS` for that provider.
4. Add installation, permission, billing, and security documentation plus a runnable example job template.
5. Test URLs, invalid inputs, CLI command contracts, error redaction, and secret lifetime using mocks.

Never pretend that all provider workflows or runner APIs are compatible. Prefer vendor-standard native agents, minimal permission scopes and repository-scoped registration. All security-sensitive commands must avoid shell interpolation of untrusted data and should not put bearer tokens in logging output or host process command-line arguments.

## Pull requests

Describe what changed, why, compatibility considerations, security implications and the output of local tests. Preserve the MIT licence and copyright notices. Maintain user-centric explanations and a neutral default brand.
