# Raeburn Talent — deployment profile (approval draft)

**Status: NOT DEPLOYABLE, NO-GO.** This is a requirement checklist; not a live target definition, set of credentials, or instruction to connect to production databases.

Application: Raeburn Talent / ATS, linked to private GitLab `Theraeburngroup/main_ats` and the GitHub `Raebu/main_ats` reference. Repository ownership, default branch, authoritative deployment target, current runtime and infrastructure inventory MUST be reconciled before enabling any deployment.

## Verified repository-specific deployment inventory — 21 October 2026

Read the actual authenticated GitLab \`Theraeburngroup/main_ats\` \`main\` tree before preparing this profile:

- \`infrastructure/production/docker-compose.production.yml\` includes seven application release units: **edge-api**, **recruitment-core**, **selection**, **experience**, **platform-control**, **intelligence-data**, and **malware-scanner**, plus infrastructure such as NATS and Caddy.
- \`infrastructure/production/deployment-units.json\` is the existing deployment unit map. It must be used by the application-specific adapter, rather than guessing service names from a generic template.
- \`infrastructure/production/Dockerfile.unit\`, \`Dockerfile.worker\`, \`Dockerfile.workspace\` and \`Dockerfile.malware-scanner\` define the existing image build family.
- \`apps/careers-web\` and \`apps/talent-admin\` are separately built Next.js frontends; the repository explicitly permits Vercel for these.
- \`scripts/deploy-release.mjs\` already submits an authorised release request to an existing deployment endpoint using an environment URL and token, an exact source SHA, pinned unit image and malware-scanner image references, the configured units, a rollout strategy and a rollback flag. **Do not replace or bypass this reviewed application controller** until its authenticated target, endpoint and actual rollback behavior have been checked.
- \`scripts/backup-service-databases.mjs\`, \`restore-drill.mjs\`, \`validate-migrations.mjs\`, \`ci-security-isolation.mjs\`, \`synthetic-candidate-journey.mjs\`, \`release-smoke.mjs\` and \`verify-stage12-live.mjs\` provide named candidate execution gates. They are not proof that those gates currently pass on the target.
- The production README describes service-specific PostgreSQL boundaries, NATS/JetStream, private object storage (R2 intended), private scanner endpoints, TLS and strict authenticated service traffic. These all require target-side credentials and isolated backing data.

Use the **existing ATS release controller** as the authoritative deployment interface once verified, with Open CI Hub as the external build/test/approval/evidence broker. Do not directly reuse the generic remote Compose path to bypass its domain-specific release rules.

## Required release record

- Source: exact 40-character Git commit; tested main/protected branch and release review/approvals.
- Artifacts: immutable API gateway, backend/worker, frontend(s) and ClamAV images or frontend bundles, each digest-pinned and scanned.
- Security gates: vacancies approval locking, tenant auth and object access; malware input including EICAR fixture; CV upload quarantine, sanitisation, PII stripping, signed download links and temporary-file destruction.
- CI gates: typecheck/build/lint, unit/integration/e2e, database migrations, CV handling regression, source dependency audit, image scan and SBOM/provenance.
- Staging prerequisites: isolated staging infrastructure and database (never production data), approved DNS/TLS, secrets supplied by target-side manager and non-root deploy identity.
- Database: versioned PostgreSQL migration plan, backup/restore proof, backward-compatible migration checks, connection pooling and appropriate protected secret access.
- Deployment order: non-destructive backup → database compatibility check → migrations if reversible/approved → API gateway and workers → frontend(s) → ClamAV scanner availability verification → authentication and public application paths.
- Health probes: API ready/live, scanner available and current, database connectivity, candidate public application flow, recruiter login + vacancy approval, audit event recorded.
- Failure: stop promotion; capture logs with PII redaction, mark exact stage failed, restore previous images/frontends only when schema compatible, verify health again; database restore is NEVER automatically attempted without a separately approved recoverability plan.
- Production release: explicit owner-approved policy, staging proof for the **same** revision, full security and business journey checks, backup/rollback verification, final GO authorisation and evidence links.

## Missing verified configuration

The current conversation has no authoritative ATS staging SSH host, service inventory, build image digests, registry, production domain health path, test credentials or safe backup/restore proof. Therefore deployment actions must remain **disabled** until those are inspected and approved via connected GitHub/GitLab and local Open CI Hub.

Track development and release gates in [Open CI Hub deployment issue #49](https://github.com/Raebu/open_ci_hub/issues/49). Track actual progress in the existing [Raeburn Recruitment CRM Master Project Delivery Tracker](https://docs.google.com/spreadsheets/d/1XskpVsVOaq4Fzm5qwdyKbqZ2_F1ZDJcUbZIuZN8mJgA/edit) and [Open CI Hub canonical delivery issue](https://github.com/Raebu/open_ci_hub/issues/48). Keep a clear distinction between the ATS and any similarly named older recruitment CRM project; do not silently equate repositories.
