# Mandatory zero-additional-cost operating policy

Open CI Hub must run on existing authorised hardware and connections with no new recurring subscriptions, metered cloud compute or paid CI minutes. Hardware, electricity, internet and separately hosted applications are excluded from this claim. This requirement takes precedence over provider convenience features.

## Architecture and enforced boundaries

Keep the existing Python/SQLite task ledger, resource broker, local Docker workers, local logs/artifacts and systemd user services. No paid queue, database, monitoring or cloud runner is required. Use the existing GitHub/GitLab accounts and approved credentials. Do not create another Hub service.

`cost_policy.py` denies native GitHub, GitLab and CircleCI retry operations before provider calls: a mixed pipeline could include billable hosted jobs. Retry through the Hub queue instead. Cancellation and inspection remain available.

Cloudflare and Vercel promotion/rollback adapters now fail closed before credentials or requests. Account plan, hard spending controls and commercial eligibility have not been verified for these actual accounts. A config flag or environment variable cannot waive this boundary. Existing deployments, subscriptions and resources are preserved. These adapters require a reviewed admission implementation and explicit owner approval before an exception; this version supplies no exception mechanism. Local release functionality is unaffected. SSH targets retain their existing deployment permission checks; operators must ensure those existing hosts are authorised and do not introduce new costs.

No automatic plan upgrades, new infrastructure provisioning, billing activation or credit purchases are authorised. Set provider spending controls to £0 where supported; an alert is not a hard cap. Unknown quota, stale usage or an inability to prevent overage must reject the operation. Provider account limits have NOT been changed by this implementation.

Paid AI is optional and must be disabled by default. The core Hub does not require an AI API. Existing unrelated personal WhatsApp modifications include an untracked paid API adapter; these were preserved and are not audited or certified by this change.

## Provider terms checked 23 October 2026

- GitHub documents self-hosted Actions runners as free to use: https://docs.github.com/en/actions/concepts/runners/self-hosted-runners . Use local Hub execution and avoid provider artifact uploads/caches that may have separate storage charges. No hosted-runner allowance is part of the Hub budget.
- GitLab says customer-owned runners do not consume its shared-runner compute-minute quota: https://about.gitlab.com/pricing/faq-compute-minutes/ . Use approved project/group runners or direct Hub jobs, never fallback to hosted instance runners. Namespace storage remains a separate allowance: https://docs.gitlab.com/user/storage_usage_quotas/ . Keep artifacts/logs local.
- Cloudflare Workers Free currently allows 100,000 requests/day and 10 ms CPU/request, returning error 1027 above the daily limit: https://developers.cloudflare.com/workers/platform/limits/ . That fact does not establish the account plan or prices of bound products. No cloud resource is needed for the Hub core.
- Vercel Hobby is restricted to personal, non-commercial use: https://vercel.com/docs/plans/hobby and https://vercel.com/docs/limits/fair-use-guidelines . It is not a free commercial hosting solution for the Raeburn Group. Preserve any separately approved existing paid hosting; do not migrate or cancel it.
- CircleCI is optional legacy integration. Its current allowances and commercial terms are unverified; no free-tier claim is made and retries are blocked.

Recheck terms before enabling provider operations; published free allowances are not proof of account billing configuration. Existing remote access/tunnel providers also need account-specific commercial eligibility and hard-limit verification before the full Hub zero-cost acceptance criterion can be certified.

## Verification and remaining scope

Tests assert that blocked retries and cloud promotions/rollbacks make no CLI, credential or network calls. Existing adapter protocol tests mock admission explicitly; passing protocol tests do not imply production billing approval.

This policy is not a sandbox for arbitrary owner-authorised shell commands, repository dependency scripts or external actors. They can independently call paid providers; capability-scoped credentials and network restrictions must enforce their boundary. The full master task (private-repository push proof, connector provenance, end-to-end deployments, all provider account limits and installed-commit reconciliation) remains incomplete until demonstrated with real evidence. Never report completion merely from unit tests.

The Hub bridge now uses `gh auth git-credential` or `glab auth git-credential` per clone, resetting inherited helper selection for that invocation. It does not change global Git configuration. Terminal prompting is disabled. On 23 October 2026 the existing GitLab CLI identity successfully read Directors Lounge project metadata and `git ls-remote` returned HEAD `25cad22d312b19f5d44cfee980a5c3d040fd25de`. Write access, rotation/expiry recovery and a full isolated integration build are not yet demonstrated.
