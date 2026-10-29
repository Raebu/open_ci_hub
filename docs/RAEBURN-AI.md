# RaeburnAI — local quantised model with governed transaction learning

Status: deployed and independently tested on 29 October 2026.

## Model and execution

- Identity: **RaeburnAI**, based on Qwen/Qwen3.5-2B (Apache-2.0).
- Quantised inference: **Q4_K_M GGUF**, 1,396,198,496 bytes.
- Pinned source: bartowski/Qwen_Qwen3.5-2B-GGUF at repository commit
  7d26695454df6de5fbcce2e58681e62dae06ce43.
- Source and target SHA-256 verified:
  57a1085840f497d764a7fc5d346922dbde961efb54cc792ea81d694fd846a1d8
- Samsung Galaxy Book Go Debian 13 ARM64, system service oci-raeburnai.
- Loopback-only endpoint **127.0.0.1:8879**, never exposed to public/tailnet
  clients except through authenticated Tailscale SSH and the bounded stdin
  request bridge.
- Restricted Linux account ociworker, systemd RAM maximum 2300M, CPU 550%.
- On-device first inference: exact RAEBURN_READY; private SSH bridge test:
  exact RAEBURN_AI_OK.
- Qwen3 0.6B remains a separate restricted local service at 127.0.0.1:8878;
  RaeburnAI CLI uses it only when the primary model is unavailable.
- GPU acceleration not enabled; both models are CPU-only. Samsung has only
  ~3.4 GiB usable RAM, so heavy parallel tasks can exhaust memory.

## Immediate learning: evidence memory (NOT weight updates)

The Razer runs raeburn_ai.py against an owner-private SQLite/FTS5 database
in .local/raeburn-ai/memory.sqlite (0600; enclosing directory 0700).

The systemd --user service raeburn-ai-sync.service and persistent timer
raeburn-ai-sync.timer ingest up to 250 new operations-ledger stage changes
per run, automatically every ~2 minutes. It reads the existing
.local/business-execution/control.sqlite in READ-ONLY mode.
Already imported: **698** business-operation stage transitions at last check;
this count continues as the upstream source grows.

Safety:
- No raw transaction payload, source_ref, names, account numbers, money
  amounts, customer records, private email bodies or tokens are copied.
- The imported fields are restricted to lane/kind, stage transition, event
  timestamp, source ID and SHA-256 evidence digest. Metadata is scoped to
  the *raeburn-group* tenant because the canonical business ledger does
  not establish a separate subsidiary ownership for each row.
- Records are flagged OBSERVED/UNVERIFIED, never assumed true. They are
  retrievable only using explicit --include-observations. Verified,
  human-approved operating lessons can be used in default retrieval.
- Replays are idempotent. The importer stores a durable source cursor.
- Tenant-isolated retrieval prevents an unapproved subsidiary's records
  from appearing in another tenant's query.
- Payment, CRM, GIBP and email systems are **not** connected to the
  learning importer; connecting these would require an approved, field-
  allowlisted integration and compliance checks before any ingestion.

## Use from the Razer (fish-compatible)

    cd /mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub
    python3 raeburn_ai.py status
    systemctl --user status raeburn-ai-sync.timer

    # RaeburnAI uses only approved operating knowledge by default.
    printf 'What do we know about ARM64 deployment?' |
        python3 raeburn_ai.py ask

    # Explicitly consider unverified historical stage changes:
    printf 'Which product operations changed stage?' |
        python3 raeburn_ai.py ask --include-observations

    # Add a short approved, non-personal lesson (only after verifying it):
    python3 raeburn_ai.py remember --category technical_lesson \
      --fact 'Use independent checksum validation after each model transfer.' --verified

The full question is transmitted only over private Tailscale SSH stdin to
Samsung's fixed, local inference endpoint, not to cloud APIs.

## Actual weight fine-tuning (NOT yet performed)

Training the 4-bit inference GGUF in-place after each transaction is **not**
appropriate or supported on this Samsung. It would consume excessive RAM
and mix unverified evidence into model weights. Instead:

1. Capture operational transactions immediately into searchable memory.
2. Human-review specific outcomes against the underlying receipts.
3. Submit a **minimised, consented, tenant-scoped training example** linked
   to an existing evidence SHA-256; examples must not include personal data
   or secrets. The submit interface accepts JSON on stdin.
4. Once at least 30 examples are approved, export a versioned, private
   train/held-out-validation JSONL dataset. The export changes **no weights**.
5. Fine-tune the **bf16 base model** Qwen/Qwen3.5-2B with a LoRA adapter in
   a separately approved GPU environment, evaluate accuracy, hallucinations,
   privacy leakage and regressions against the held-out examples, and
   require a human review/rollback plan before promotion.
6. Convert/evaluate a new Q4_K_M GGUF for Samsung inference only if its
   results are at least as good as the preceding model.

Unsloth documents Qwen3.5 2B bf16 LoRA fine-tuning with approximately
5 GB VRAM, and explicitly warns against QLoRA 4-bit fine-tuning for
Qwen3.5 because of high quantisation differences:
https://unsloth.ai/docs/models/qwen3.5/fine-tune

The Samsung lacks sufficient memory and GPU support for such training.
No paid cloud training or unauthorised uploading of business examples is
enabled.

### Training review CLI

    python3 raeburn_ai.py training-preview
    # Provide a minimal JSON {prompt,expected} from stdin, plus a real
    # evidence SHA from an existing tenant-scoped memory entry:
    cat /path/to/approved-example.json |
      python3 raeburn_ai.py approve-example --reviewer owner_reviewed \
        --evidence-sha256 <actual-sha256>
    python3 raeburn_ai.py export-dataset

If there are fewer than 30 independently reviewed examples the dataset
export refuses. No automatic model fine-tune or deployment is scheduled.

## Verification

- Source and target model SHA-256 matched; tested one-shot model inference.
- A real local RaeburnAI answer retrieved six observed operational records,
  cited event IDs and flagged that their evidence was not independently
  verified, using Qwen3.5 2B and no external cloud calls.
- 37 targeted RaeburnAI/free-router/dashboard tests passed before final
  regression review.
- The legacy smaller Qwen service and Open CI Hub worker remain active.
- The operations importer and AI model service both start with Debian/Razer
  respectively and are separate from the foreground desktop.

## Status limitations

This is a working **RaeburnAI v1 evidence-based operational assistant**,
not a fully retrained proprietary foundation model or a system trained on
all subsidiaries' financial transactions. Future updates require authorised
data connectors, reviewed examples and safe compute for fine-tuning.
