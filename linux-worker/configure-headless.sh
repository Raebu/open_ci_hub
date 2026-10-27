#!/usr/bin/env bash
# Galaxy Book Go Debian ARM64: safely stage command-line-only configuration.
# Never partitions, flashes or modifies kernel/boot files.
set -euo pipefail
MODE="${1:-}"
HUB='https://theraeburngroup.tail8450a3.ts.net'
case "$MODE" in
  --check)
    uname -sm
    grep -E 'MemTotal:|MemAvailable:' /proc/meminfo
    systemctl is-active ssh || true
    if command -v tailscale >/dev/null; then tailscale ip -4 || true; fi
    systemctl is-active open-ci-edge-worker || true
    exit 0 ;;
  --prepare|--headless|--install-worker) ;;
  *) echo >&2 'Usage: sudo bash linux-worker/configure-headless.sh --prepare|--headless|--install-worker|--check'; exit 2 ;;
esac
[[ "$(id -u)" == 0 ]] || { echo >&2 'Run as root using sudo.'; exit 1; }
[[ "$(uname -m)" == 'aarch64' ]] || { echo >&2 'Not ARM64 Linux; refusing.'; exit 1; }
grep -Eqi 'Debian|Trixie' /etc/os-release || { echo >&2 'Expected Debian ARM64; refusing.'; exit 1; }
if [[ "$MODE" == '--prepare' ]]; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    python3 openssh-server ca-certificates curl
  systemctl enable --now ssh
  echo 'Now set up authorised private Tailscale and verify remote SSH.'
  exit 0
fi
systemctl is-active --quiet ssh || { echo >&2 'SSH service is not active.'; exit 1; }
command -v tailscale >/dev/null || { echo >&2 'Tailscale missing.'; exit 1; }
tailscale ip -4 | grep -Eq '^100\.' || { echo >&2 'Tailscale not connected.'; exit 1; }
if [[ "$MODE" == '--headless' ]]; then
  echo 'Changing default boot target only (no packages removed).'
  systemctl set-default multi-user.target
  echo 'Remote rollback: sudo systemctl set-default graphical.target'
  exit 0
fi
curl -fsS --max-time 15 "$HUB/health" >/dev/null || {
  echo >&2 'Private Hub not reachable or TLS invalid.'; exit 1;
}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[[ -f "$ROOT/portable_edge_worker.py" && -f "$ROOT/application_client.py" ]] || {
  echo >&2 'Run from the current Open CI Hub repository checkout.'; exit 1;
}
if systemctl is-active --quiet open-ci-edge-worker 2>/dev/null; then
  echo >&2 'A worker is already running; refusing replacement.'; exit 1
fi
echo 'Razer must enrol a Linux ARM64 worker and issue one NEW worker-only credential.'
read -r -s -p 'Enter NEW worker token (hidden): ' TOKEN
echo
if [[ ! "$TOKEN" =~ ^oci_v1_[A-Za-z0-9_-]+$ ]]; then
  unset TOKEN
  echo >&2 'Credential format invalid.'; exit 1
fi
id -u ociworker >/dev/null 2>&1 || \
  useradd --system --home-dir /var/lib/open-ci-hub --create-home --shell /usr/sbin/nologin ociworker
install -d -m 0755 -o root -g root /opt/open-ci-hub
install -m 0644 -o root -g root "$ROOT/portable_edge_worker.py" /opt/open-ci-hub/
install -m 0644 -o root -g root "$ROOT/application_client.py" /opt/open-ci-hub/
install -d -m 0700 -o root -g root /etc/open-ci-hub
umask 077
printf 'OCI_EDGE_URL=%s\nOCI_EDGE_WORKER_TOKEN=%s\n' "$HUB" "$TOKEN" > /etc/open-ci-hub/worker.env
unset TOKEN
chmod 0600 /etc/open-ci-hub/worker.env
cat > /etc/systemd/system/open-ci-edge-worker.service <<'UNIT'
[Unit]
Description=Open CI Hub restricted ARM64 Linux edge worker
Wants=network-online.target
After=network-online.target tailscaled.service
[Service]
Type=simple
User=ociworker
Group=ociworker
WorkingDirectory=/opt/open-ci-hub
EnvironmentFile=/etc/open-ci-hub/worker.env
ExecStart=/usr/bin/python3 -u /opt/open-ci-hub/portable_edge_worker.py --interval 60
Restart=on-failure
RestartSec=30
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectControlGroups=true
UMask=0077
MemoryMax=384M
CPUQuota=50%
[Install]
WantedBy=multi-user.target
UNIT
chmod 0644 /etc/systemd/system/open-ci-edge-worker.service
systemctl daemon-reload
systemctl enable --now open-ci-edge-worker
echo 'Worker staged; verify Razer heartbeat + an actual job receipt.'
