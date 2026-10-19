# Signed, rollback-capable user service deployments

Open CI Hub can now locally stage an immutable signed .tar.gz package and switch a preconfigured user service to that release. Deploy/rollback are deliberately **not yet exposed through an unauthenticated CI event or general-purpose mobile shell**. This CLI runs under the Linux user, never as root.

## Approval and trust

A signed release must contain a release.json file with:

~~~json
{
  "application": "serviceapp",
  "source_commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "reviewed_by_signer": true,
  "checks": {
    "tests": {"status": "passed", "log_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},
    "build": {"status": "passed", "log_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"},
    "database": {"status": "passed", "log_sha256": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"}
  }
}
~~~

This metadata is a *signed statement by the release approver*, not itself proof that these checks ran. Independent attestation of worker actions and the actual database results must be validated separately before the approver signs. The automated local receipt described in docs/EXECUTION-EVIDENCE.md is only runner-reported.

Store an approved OpenSSH signer public key file at .local/deployment-allowed-signers, using allowed signers syntax such as "reviewer ssh-ed25519 AAAA...". This file must be installed by the host owner. Store the signer **private** key offline; never in the repository, hosted CI variables, the runner or deployment state.

### Produce signed package offline

Prepare a validated .tar.gz archive of the application with release.json at its root. On the trusted machine with the private key:

~~~bash
bash scripts/sign-distribution.sh package.tar.gz /path/to/PRIVATE_OFFLINE_SIGNER
~~~

This produces package.tar.gz.sig (OpenSSH signature namespace open-ci-hub-deploy) and package.tar.gz.sha256. The private key never goes to CI.

### Configure one user service (once per PC)

~~~bash
python3 release_manager.py allow serviceapp serviceapp.service --health-port 37771 --health-path /health --confirm
~~~

The pre-existing user systemd service must reference .local/deployments/serviceapp/current as its release directory. The service must be designed to start from that location, provide an appropriate local HTTP health endpoint and stop/restart safely without losing its working data. **Do not place mutable databases or uploads inside a release directory.**

### Stage, deploy, rollback

~~~bash
python3 release_manager.py stage serviceapp package.tar.gz package.tar.gz.sig --sha256 FULL_64_CHARACTER_DIGEST --signer reviewer --confirm
python3 release_manager.py deploy serviceapp FULL_64_CHARACTER_DIGEST --confirm
python3 release_manager.py status serviceapp
python3 release_manager.py rollback serviceapp --confirm
~~~

The stage verifies SHA-256 and the OpenSSH signature with the separately approved offline public key, rejects tar traversal, links, devices, duplicate/large items, demands source commit plus tests/build/database check metadata, and stores a read-only inventory to detect changes before activation. Releases are under private .local/deployments/APP/releases/DIGEST directories and never overwritten.

Deploy checks every staged file's digest, atomically switches the relative current release symlink, restarts the allowed systemd **user** unit and performs a loopback-only health check. If restart or health fails, it restores the old pointer and attempts to restart the previous service. Successful deploy records the previous release; rollback changes back to it and validates health.

## Material limits

This is a safe **local deployment mechanism**, not a claim of fully verified zero-downtime production release. It does not validate actual external URLs, database state, service dependencies, rollout to multiple PCs, container orchestrators, or recover data after destructive migrations. Database rollback requires a distinct backup and recovery plan and verified restore drill. Signed packages prove that an approved release signer signed those bytes, not that every claimed test was independently executed.

Full production rollout should use a dedicated authorised controller with deployment locks, staging smoke tests, protected branch rules, live application health, independent approval, signed provenance, explicit recovery owners and structured incident logs.
