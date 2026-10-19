# CI execution evidence — verify what actually ran

Open CI Hub must not label a pipeline production-ready merely because GitHub/GitLab reported a green state. A seven-second status change is **not** independent proof that the test suite, build and database checks ran.

## Evidence levels

1. **Not proven** — a provider pipeline exists but execution receipts are absent, or a job never acquired a runner. GitLab failures such as ci_quota_exceeded with runner=null fall into this category.
2. **Runner reported** — a self-hosted native job produced an execution receipt and a retained artifact. The receipt contains invoked command arguments, observed process exit code, duration, test count when a unittest summary exists, sanitized log SHA-256 and exact commit/run ID. The artifact's presence is independently queryable from the provider but its *contents* are runner-reported.
3. **Locally consistent** — after downloading the retained receipt and log as files, python3 execution_evidence.py verify PATH/receipt.json checks that the transcript digest matches, all step exits were zero and the unittest summary records at least one actual test. This confirms internal integrity, **not independence from the runner**.
4. **Independent attestation** — NOT IMPLEMENTED. Would require an independently controlled/verifiably isolated executor and signing keys unavailable to the job being attested, with protected source commit, exact build inputs, dependency locks and complete step list verified separately.

Open CI Hub deliberately reports independent_attestation=false until such evidence exists. A malicious/compromised runner can forge command receipts and digests. GitHub itself may truthfully mark the runner job successful even when the workflow had no meaningful tests.

## Producing evidence in a trusted workflow

~~~yaml
- name: Record full Python test execution
  run: python3 execution_evidence.py run --name python-unittest --output .local/ci-proof/receipt.json --timeout 300 -- python3 -m unittest discover -s tests -v
- name: Verify receipt against transcript
  if: always()
  run: python3 execution_evidence.py verify .local/ci-proof/receipt.json
- name: Upload proof
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: open-ci-hub-execution-proof
    path: |
      .local/ci-proof/receipt.json
      .local/ci-proof/python-unittest.log
    if-no-files-found: error
~~~

The project's native self-hosted CI now follows this pattern. The log and receipt are kept in git-ignored .local/ and retained by GitHub's artifact store, normally for seven days. Don't include secret-containing commands or log raw passwords; sanitisation is best-effort, not an excuse to print secrets.

## Where to inspect

On the mobile or desktop dashboard use **Pipelines → Jobs, logs & artifacts**. The **Execution proof** section shows actual provider-run job names, runner assignment, job statuses and step results where returned. A missing native runner or ci_quota_exceeded is displayed, not marked ready.

The receipt may be downloaded from the provider artifacts list and checked locally with:

~~~bash
python3 execution_evidence.py verify path/to/receipt.json
~~~

A green job without a receipt remains Not proven. Future project integrations should record and verify **each required check**, including database migration checks and builds, not assume that a single fast smoke test covers them.

## Service Business proof rule

A deployment gate should demand explicit mandatory check names and a matching commit SHA; require recorded logs and successful exits for every check; verify real database tests occurred against a suitable disposable database; and independently validate the resulting release artifact signature before exposing production traffic. It must refuse to proceed if any such evidence is missing. This repository's own Python test suite does not imply those Service Business checks have run.
