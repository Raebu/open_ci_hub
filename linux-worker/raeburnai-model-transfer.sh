#!/usr/bin/env bash
# Verified public model transfer to the Samsung over Tailscale, no Windows or existing model changes.
set -euo pipefail
SRC=/mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub/.local/ai-models/Qwen_Qwen3.5-2B-Q4_K_M.gguf
PART=/opt/open-ci-hub/ai/RaeburnAI-Qwen3.5-2B-Q4_K_M.gguf.part
FINAL=/opt/open-ci-hub/ai/RaeburnAI-Qwen3.5-2B-Q4_K_M.gguf
HASH=57a1085840f497d764a7fc5d346922dbde961efb54cc792ea81d694fd846a1d8
echo "$HASH  $SRC" | sha256sum --check --status
echo "RAEBURNAI_MODEL_SOURCE_VERIFIED"
cat "$SRC" | timeout 1600 tailscale ssh root@samsung-book-go "cat > $PART"
timeout 100 tailscale ssh root@samsung-book-go "test \$(stat -c%s $PART) = 1396198496 && echo '$HASH  $PART' | sha256sum --check --status"
timeout 20 tailscale ssh root@samsung-book-go "mv $PART $FINAL && chmod 0644 $FINAL"
echo "RAEBURNAI_MODEL_TARGET_SHA256_VERIFIED"
