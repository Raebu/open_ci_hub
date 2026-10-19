# Release verification checklist

## Offline repeatable checks

```bash
python -m compileall -q hub.py web_console.py operations.py audit.py providers tests
python -m unittest discover -s tests -v
node --check dashboard/app.js
python hub.py doctor
```

Check `.local/`, tokens, dotenv files, caches and credentials are excluded from the packaged ZIP and Git. You must not commit test state.

## Controlled test machine (required before using as production)

- [ ] Install supported versions of Docker Desktop/Engine + Compose V2 and official `gh`, `glab`, `circleci` CLI.
- [ ] Browser sign-in completes for each provider, with the minimum necessary scopes.
- [ ] Build the GitHub runner image (`docker compose --profile github build github`) and inspect the mounted `/home/runner` volume; confirm `config.sh` and `run.sh` remain present after registration and restarts.
- [ ] Register a test repository/project runner through the dashboard and confirm native runner appears in the provider's UI.
- [ ] Run a small tagged test on each provider; confirm job appears in pipeline cockpit and in provider UI. GitLab branch must be protected and job tagged `local-ci`.
- [ ] Restart runner container and PC; verify runner reconnects and retained credentials are not shown in UI or logs.
- [ ] Retry a failed test pipeline and cancel a running test pipeline with explicit UI confirmation. Check audit records and provider results.
- [ ] Create private test repositories. Verify owner/group permissions and confirm that a public repository cannot be created without an explicit acknowledgement.
- [ ] Test Tailscale Serve from an approved phone; test pending, viewer (cannot control), editor, revoke, and disallowed identity.
- [ ] Switch PC off: mobile dashboard should be unavailable; there is *no hosted account*.
- [ ] Deregister every test runner and revoke its provider token. Clear test repositories where appropriate.
- [ ] Complete independent review of CI job security, secrets, headers, origin/CSRF and installer code.

Until completed, release status is **community preview**, not production proof.
