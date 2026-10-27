#!/usr/bin/env bash
# Non-destructive: downloads and SHA-256 verifies Galaxy Book Go image.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$ROOT/.local/galaxy-book-go"
mkdir -p "$DIR"
chmod 700 "$DIR"
NAME="snapdragon_7c_woa-aarch64-trixie-260610.img.gz"
SHA="8794e8b99571f448bd9019ade34785727c0cae59bcdffb1aee631d9566e2f094"
URL="https://github.com/velvet-os/imagebuilder-testimages/releases/download/spring-2026-testimages/$NAME"
if [[ -f "$DIR/$NAME" ]]; then
  echo "$SHA  $DIR/$NAME" | sha256sum --check
  exit 0
fi
echo "Downloading experimental image (1.87 GB); no disks will be overwritten."
curl --fail --location --retry 3 --retry-delay 3 --continue-at - --output "$DIR/$NAME.part" "$URL"
echo "$SHA  $DIR/$NAME.part" | sha256sum --check
mv -- "$DIR/$NAME.part" "$DIR/$NAME"
echo "Verified image staged at $DIR/$NAME"
