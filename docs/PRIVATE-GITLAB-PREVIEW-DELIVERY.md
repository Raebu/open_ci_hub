# Owner-private GitLab signed preview delivery

**Source-only. Not installed, enrolled, authorised or deployed on Garuda.**

`gitlab_signed_preview_delivery.py` adds private GitLab issue transport to the Ed25519 preview release chain:

1. The normal PC owner enrols one exact **private GitLab control project**, pinned numeric project ID, trusted issue authors, source pipeline actor and artifact job name.
2. The receiver reads matching open issue descriptions. Issues are **transport, not authorisation**; only a separately issuer-signed manifest can proceed.
3. GitLab APIs are re-queried with the receiver's read-only credentials to prove the exact source project, trusted push actor, successful pipeline, immutable source SHA, eligible local-runner required jobs, job trace SHA256 digests and matching artifact ZIP bytes.
4. Source checks, logs and artifact digest must match the signed manifest exactly, before queueing a preview in the existing bounded private PC queue.
5. No deployment happens in the transport. Preview consent, stage and health/rollback are separately enforced. The signer private key must never be accessible from CI or this receiver.

Example **only after** security review and disposable live PC service setup:

```fish
python3 gitlab_signed_preview_delivery.py enrol demo \
  --control-project Theraeburngroup/private-control \
  --trusted-author Raebu \
  --source-actor Raebu \
  --artifact-job build \
  --confirm
python3 gitlab_signed_preview_delivery.py poll-once demo
python3 gitlab_signed_preview_delivery.py status demo
```

Outstanding gates: real GitLab group/project permissions, runner and pipeline API edge-case validation, private-control issue publisher, signer OS isolation, timeouts/pagination checks, actual Garuda deployment/failed-health rollback, CI status publisher issue #28 and independent security review. **Production automation and automatic AI repair remain disabled.**
