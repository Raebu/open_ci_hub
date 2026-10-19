# Provider sign-in: what 'no API keys' means

The end-user experience does not require copying a personal access token, pasting a GitLab runner token or setting up an OAuth app on a cloud host.

The project deliberately delegates authentication to **official provider CLIs** on your own PC:

- **GitHub.com**: `gh auth login --web`; token for registering a runner is issued by `gh api --method POST /repos/OWNER/REPO/actions/runners/registration-token` (or the org endpoint).
- **GitLab.com**: `glab auth login` using its Web or Device flow; `glab api ... POST user/runners` returns a one-time runner authentication token.
- **CircleCI Cloud**: `circleci auth login` using the CLI's loopback browser OAuth flow; `circleci runner token create ... --json` creates the long-lived agent token.

There are still credentials *underneath*, because the native CI systems require them. GitHub and GitLab agent state remains in the official runner's persistent Docker volume. CircleCI's agent token is saved locally to `.local/circleci.env`, with restricted POSIX permissions, and **is not committed to Git**. Provider CLIs store account sign-in credentials in the system keyring when available, but may fall back to local configuration on some operating systems. GitHub and GitLab project administrators must authorise runner creation.

## Permissions and privacy

- Auth is performed on the provider's own website, not through a third-party login form. No hosted intermediary receives credentials.
- The hub does not print runner tokens to the terminal, and stores no personal access token, client secret or OAuth refresh token.
- The CircleCI worker necessarily needs an agent credential after restarts. Protect the `.local` folder and your Docker administrators.
- Official CLIs may request broad scopes. Inspect the consent screen carefully; if you do not approve, the runner cannot be created automatically.
- A *separate* OAuth app is needed for some **self-managed GitLab** instances, depending on their configuration. For GitHub Enterprise or CircleCI Server, build an appropriate instance-specific adapter before claiming support.
- Signing into one provider does **not** automatically log you into the others, and does not give one provider access to another's repositories.

This is not 'log in with GitHub to sign in to all services'. It is **provider-specific browser sign-in** from one installer with no manual token copying.

Sources: [GitHub CLI auth](https://cli.github.com/manual/gh_auth_login), [GitHub runner token API](https://docs.github.com/en/rest/actions/self-hosted-runners), [GitLab CLI auth](https://docs.gitlab.com/cli/authentication/), [GitLab runner API](https://docs.gitlab.com/api/runners/), [CircleCI CLI reference](https://cli.circleci.com/reference/).
