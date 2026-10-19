#!/usr/bin/env bash
# Sign a release package with an OFFLINE/owner-held SSH key (never in CI).
set -euo pipefail
if [ "$#" -ne 2 ]; then
  echo "Usage: bash scripts/sign-distribution.sh PACKAGE.tar.gz /path/to/OFFLINE_SIGNING_KEY" >&2
  exit 2
fi
bundle="$(realpath -- "$1")"
key="$(realpath -- "$2")"
root="$(cd "$(dirname "$0")/.." && pwd -P)"
case "$key" in "$root"/*)
  echo "Refusing a signing key inside the source repository." >&2
  exit 1 ;;
esac
[ -f "$bundle" ] || { echo "Release bundle is missing." >&2; exit 1; }
[ -f "$key" ] || { echo "Private signing key missing." >&2; exit 1; }
[ ! -e "$bundle.sig" ] || { echo "Signature already exists: will not overwrite." >&2; exit 1; }
ssh-keygen -Y sign -f "$key" -n open-ci-hub-deploy "$bundle" >/dev/null
sha256sum "$bundle" > "$bundle.sha256"
echo "Signed bundle; share $bundle, $bundle.sig, $bundle.sha256 and the signer identity."
echo "Keep the signing private key away from your runner, repository and deployed computer."
