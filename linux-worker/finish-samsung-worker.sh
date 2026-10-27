#!/usr/bin/env bash
# Owner-invoked completion of the Samsung Linux worker enrollment.
# Runs on Razer only. Reads the previously issued, device-scoped worker
# credential from .local (gitignored) and sends it over authenticated
# Tailscale SSH to the owner-owned Samsung. No secrets printed or logged.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
HOST='100.80.206.122'
APP='samsung-linux-edge'
CREDS='.local/worker-credentials/samsung-linux-edge-worker.issue.json'
[[ -f "$CREDS" && ! -L "$CREDS" ]] || {
  echo >&2 'Expected owner-issued worker credential is missing/unsafe.'
  exit 1
}
[[ "$(stat -c %a "$CREDS")" == 600 ]] || {
  echo >&2 'Credential file must have mode 0600.'; exit 1;
}
command -v tailscale >/dev/null
command -v python3 >/dev/null
tailscale status --json | python3 -c '
import json,sys
peers=json.load(sys.stdin).get("Peer",{}).values()
matching=[p for p in peers if p.get("HostName")=="samsung-edge"
          and "100.80.206.122" in p.get("TailscaleIPs",[])
          and p.get("OS")=="linux" and p.get("Online") is True]
if len(matching)!=1: raise SystemExit("Verified Samsung Linux Tailnet peer is not online.")
'
echo 'Verified Tailscale peer samsung-edge is online. Sending one scoped worker secret.'
# Python stdout is piped straight to encrypted SSH; never display or save it.
python3 -c '
import json,sys
with open(sys.argv[1],encoding="utf8") as handle: credential=json.load(handle)
if credential.get("app")!="samsung-linux-edge" or credential.get("scope")!="worker":
    raise SystemExit("Wrong app/role in credential receipt.")
token=credential.get("token")
if not isinstance(token,str) or not token.startswith("oci_v1_") or len(token)>256:
    raise SystemExit("Credential value is invalid.")
sys.stdout.write("OCI_EDGE_URL=https://theraeburngroup.tail8450a3.ts.net\n")
sys.stdout.write("OCI_EDGE_WORKER_TOKEN="+token+"\n")
' "$CREDS" | tailscale ssh root@"$HOST" '
set -eu
install -d -m 0700 /etc/open-ci-hub
[ -f /opt/open-ci-hub/portable_edge_worker.py ] || exit 2
[ -f /etc/systemd/system/open-ci-edge-worker.service ] || exit 2
umask 077
TMP="$(mktemp /etc/open-ci-hub/.worker-env.XXXXXXXX)"
trap '\''rm -f "$TMP"'\'' EXIT
cat > "$TMP"
[ "$(wc -l < "$TMP")" -eq 2 ]
grep -q "^OCI_EDGE_URL=https://theraeburngroup.tail8450a3.ts.net$" "$TMP"
grep -q "^OCI_EDGE_WORKER_TOKEN=oci_v1_" "$TMP"
chmod 0600 "$TMP"
mv -f -- "$TMP" /etc/open-ci-hub/worker.env
systemctl enable --now open-ci-edge-worker.service
echo "Service state: $(systemctl is-active open-ci-edge-worker)"
echo "Service startup: $(systemctl is-enabled open-ci-edge-worker)"
'
echo 'Enrolment complete. Check the Razer registry for online heartbeat.'
