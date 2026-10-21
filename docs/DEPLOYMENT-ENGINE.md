# Open CI Hub — native deployment engine: delivery and release gates

The target architecture is: GitHub/GitLab verified event → shared resource-aware broker → exact-SHA checkout → isolated build/test/security checks → immutable signed artifact → approved environment promotion → health and user-journey checks → safe rollback / incident evidence.

There is ONE CI scheduler, one existing signed local release manager, and a new environment-aware deployment controller. Do not create another worker framework.

## Implemented and tested in this increment (2026-10-21)

`deployment_engine.py` wraps `release_manager.Releases` for **local user-service staging only**. It supports:
- owner-controlled local target policy file, with schema validation and fail-closed target adapters;
- exact source commit SHA and signed immutable release digest checks using the existing signed release inventory;
- required signed release claims for tests, build, security, database and backup, retaining SHA-256 log identifiers;
- nonblocking flock per target so two controllers cannot deploy simultaneously to the same environment;
- append-only, hash-chained local JSONL history, verified on read (tamper-evident, not a remote/cryptographic audit service);
- explicit operator confirmation, local service restart and loopback health check from existing `release_manager.py`;
- automatic local rollback attempt by the existing release manager on failed restart/health, recording failure and uncertainty;
- read-only `plan` and `status` CLI commands, and a `deploy` command gated by confirmation.

No live application was staged or deployed as part of this implementation. Existing user services and Docker volumes are untouched.

## Supported deployment targets

| Adapter | Current state | Required before use |
| --- | --- | --- |
| Local systemd user service | **Staging only**, with signed archive already staged in the existing release manager | Owner-approved local service and verified `/health` |
| SSH + Docker Compose on an approved VPS | **Disabled; fails closed** | Strict host-key pinning, separate account, registry digest checks, guarded Compose deployment, verified previous image and safe rollback |
| Cloudflare Workers | **Disabled; fails closed** | Scoped token via private OS credential store, version deployment IDs, preview→production policy, real HTTP/TLS/auth tests and rollback |
| Vercel | **Disabled; fails closed** | Scoped token, immutable deployment ID, preview→production promotion and alias rollback verified |
| Production of any target | **Disabled** | Environment-isolated pointers and release history, staging promotion evidence, policy-approved release window and rollback, explicit auto-release approval controls |

The target manifest does not and must not contain passwords, tokens, raw SSH private keys, shell snippets or arbitrary commands. Credentials belong in separately authorised secret stores or system services; the local CI container cannot read them. Merely checking a CLI into PATH is not credential approval.

## Sample policy, not installed on the live machine

```json
{
  "schema": 1,
  "targets": {
    "open-ci-hub-staging": {
      "application": "open-ci-hub",
      "environment": "staging",
      "adapter": "local-user-service",
      "enabled": false
    },
    "open-ci-hub-production": {
      "application": "open-ci-hub",
      "environment": "production",
      "staging_target": "open-ci-hub-staging",
      "adapter": "local-user-service",
      "enabled": false
    }
  }
}
```

Once independently reviewed, create a private `.local/deployment-engine/targets.json` with 0600 permissions. Never commit this live file. A target is only eligible when `enabled: true` **and** the existing release manager has separately allowed the user service, verified the external OpenSSH signer and staged an immutable signed archive.

Read-only preview:

```bash
python3 deployment_engine.py plan open-ci-hub-staging EXACT_40_CHARACTER_COMMIT SIGNED_64_CHARACTER_DIGEST
python3 deployment_engine.py status open-ci-hub-staging
```

Staging deployment needs a separately authorised signed archive and an explicit operator command:

```bash
python3 deployment_engine.py deploy open-ci-hub-staging EXACT_40_CHARACTER_COMMIT SIGNED_64_CHARACTER_DIGEST --confirm
```

There is **no automatic deployment from a push event**, no generic remote deploy shell, and no fully verified production pathway in this increment. `--confirm` cannot bypass the signed artifact checks, policy gates or target lock. An unhealthy release may leave the target requiring manual recovery if restoring/restarting the former service also fails; a pointer rollback is not equivalent to full database rollback.

## ATS / Raeburn Talent deployment profile

The ATS is an application-specific profile, not special code inside Open CI Hub.

Before enabling any real ATS staging target, the approved manifest must identify and verify:
- GitHub/GitLab repo, protected branch and exact tested source SHA.
- Build dependencies for frontend(s), API gateway and services, database image(s) and ClamAV scanner image, all digest pinned.
- Unit, integration, authentication, tenant-boundary, CV/PII sanitisation, malware/quarantine and recruiter/vacancy approval tests.
- PostgreSQL backup proof, forward-only expand/contract migration compatibility, restore drill and an explicit decision on irreversible migrations.
- Staging domain, TLS certificate, API health probes, frontend and candidate/recruiter user journeys.
- Registry destination and immutable image names/digests; no secrets in logs or Git history.
- Staging deployment operator/owner, controlled production approval, health thresholds and previous release rollback IDs.
- GDPR, access authorisation, audit logging and release evidence appropriate to candidate data.

Do not infer production readiness from earlier unit-test passes or a GitHub-hosted placeholder check. Existing ATS infrastructure and data are left unchanged.

## Adapter integration added (controlled implementation; not activated)

The new \`deployment_providers.py\` defines provider-specific operations rather than a generic remote shell:

- **Cloudflare Workers:** look up the current single-version deployment, promote a separately built immutable UUID version to 100% via the official HTTPS API, check the approved HTTPS health URL and attempt to restore the prior version on failure. Split-traffic states fail closed.
- **Vercel:** inspect a specific already-created READY deployment, assign its exact ID to an approved alias using the official API, perform health check, and attempt to restore the previous alias assignment if unhealthy. This is an alias-based promotion, **not** an application build or an equivalent guarantee to the official full Production Promote workflow.
- **SSH / Docker Compose:** noninteractive strict host-key checking, dedicated owner-approved identity, fixed \`docker compose config --images\` / \`config -q\` / \`pull\` / \`up -d --no-build\` commands, signed digest-pinned image list comparison, isolated project name and a locked target. Automatic or production SSH rollback remains disabled pending a **real** safe recovery plan; this staging adapter cannot prove database recovery.
- **Credentials:** \`CredentialStore\` resolves identifiers to files inside \`.local/deployment-engine/credentials/\`. The directory must be owner-owned 0700 and each credential file owner-owned 0600. Secrets are never accepted through a repository manifest, provider CLI argument, stdout result or HTTP URL. Cloudflare and Vercel tokens travel only in HTTPS Authorization headers; SSH key is a private file (not in version control). Credentials must be provisioned on the target computer by the authorised owner. Do not share them in ChatGPT.
- **Signed provider reference:** a \`provider.json\` file **inside** the existing externally signed release bundle identifies each authorised target and immutable deployed version. SSH additionally demands a signed list of digest-pinned images. An unapproved commit SHA, unsigned version ID, missing previous release, or mismatched policy cannot promote.
- **Dashboard:** the authenticated existing dashboard now exposes a read-only **Deployments** section at \`/api/deployments\`, showing target, environment, latest recorded commit, previous version, failures and integrity-checked history. Only owner/editor roles can read; it has no anonymous API and no write or rollback buttons.

These adapters are code-level integrations with mocked unit tests, **not a declaration that the provider account, staging domains, application builds, secrets, migration plans, remote rollback or production site have been verified**.

For the provider configuration, prefer distinct explicit staging and production **resources**, e.g. different Cloudflare Worker scripts or different Vercel aliases; the deployment policy rejects two active targets using the same declared remote resource. The production path requires last-known-good staging evidence from the same SHA and signed artifact. Automatic release execution is blocked until independent CI attestation and a separately approved operator policy exist.

### Credential setup and staging workflow

1. Establish target inventory and an app-specific rollback/backup plan in the project tracker.
2. Provision approved scoped token/SSH key and known_hosts files under the private credential directory. Never create empty placeholder credentials.
3. Produce **and independently sign** the immutable release bundle with exact source SHA, actual test/build/security/database/backup evidence, and authorised \`provider.json\`.
4. Stage the signed bundle using existing \`release_manager.py stage\`; review the private target policy with mode 0600.
5. Run \`python3 deployment_engine.py plan TARGET SHA DIGEST\` and review the fail-closed result before using \`deploy --confirm\`.
6. Run staging-only deployment during a safe window, inspect the real deployment, health, TLS, critical user journeys and failure evidence.
7. Enable production **only after** independent CI execution receipts, verified staging, environment isolation, provider rollback drill and formal release authorisation.

The cloud targets use official HTTPS provider APIs and require **no new cloud-hosted build minutes** to promote a previously built version. They do not automatically build that version. Vercel/Cloudflare may still charge for deployed services or API usage according to account terms.

## Acceptance gates still required

P0: verify one exact-SHA real CI build and its GitHub/GitLab commit status; immutable artifact checksum and provenance; environment locking across processes and hosts; secure remote target connection and short-lived credentials; verified staging deploy/health/rollback; migration and backup/restore tests.

P1: dashboard queue and release history, exact job and prior version, bounded log excerpts, safe rollback controls, actionable provider failure status; progressive canary and smoke health; incident evidence and alerts.

P2: governed automatic production promotions after an approved policy is installed and staging succeeded for the same immutable revision. Stop on high severity security failures, unrecoverable migrations, spending/cost changes or credential rotation requiring approval.

Always obey the local machine's 4 GiB OS reserve, native-runner reservations and thermal admission rules. Never increase workload concurrency simply to make a test run while the laptop is hot.

The detailed P0/P1 deployment workstream is [issue #49](https://github.com/Raebu/open_ci_hub/issues/49). The authoritative manual-Chat acceptance tracking is [Open CI Hub project issue #48](https://github.com/Raebu/open_ci_hub/issues/48). Deployment-specific tasks are linked from there. An ordinary Chat session must update it and the existing Google Drive handover before ending.
