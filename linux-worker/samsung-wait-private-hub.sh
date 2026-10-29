#!/bin/sh
# Galaxy Book Go: wait for the private Open CI Hub to be reachable.
# This is deliberately NOT an SSH credential, browser action, or external URL.
# Runs as the restricted ociworker before the existing worker service starts.
set -eu

[ "$(hostname)" = "samsung-book-go" ] || exit 1
[ "$(uname -m)" = "aarch64" ] || exit 1
URL="https://theraeburngroup.tail8450a3.ts.net/health"
tries=0
while [ "$tries" -lt 28 ]; do
    response="$(/usr/bin/curl --silent --fail --connect-timeout 2 --max-time 4 "$URL" 2>/dev/null || true)"
    if printf '%s' "$response" | /usr/bin/grep -Eq '^\{"ok":[[:space:]]*true\}$'; then
        echo "Private Open CI Hub verified reachable"
        exit 0
    fi
    tries=$((tries+1))
    sleep 3
done
echo "Private Open CI Hub not reachable; retry via systemd restart policy" >&2
exit 1
