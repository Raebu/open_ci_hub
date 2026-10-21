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

## Acceptance gates still required

P0: verify one exact-SHA real CI build and its GitHub/GitLab commit status; immutable artifact checksum and provenance; environment locking across processes and hosts; secure remote target connection and short-lived credentials; verified staging deploy/health/rollback; migration and backup/restore tests.

P1: dashboard queue and release history, exact job and prior version, bounded log excerpts, safe rollback controls, actionable provider failure status; progressive canary and smoke health; incident evidence and alerts.

P2: governed automatic production promotions after an approved policy is installed and staging succeeded for the same immutable revision. Stop on high severity security failures, unrecoverable migrations, spending/cost changes or credential rotation requiring approval.

Always obey the local machine's 4 GiB OS reserve, native-runner reservations and thermal admission rules. Never increase workload concurrency simply to make a test run while the laptop is hot.

The detailed P0/P1 deployment workstream is [issue #49](https://github.com/Raebu/open_ci_hub/issues/49). The authoritative manual-Chat acceptance tracking is [Open CI Hub project issue #48](https://github.com/Raebu/open_ci_hub/issues/48). Deployment-specific tasks are linked from there. An ordinary Chat session must update it and the existing Google Drive handover before ending.
