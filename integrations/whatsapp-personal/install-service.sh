#!/bin/sh
# Install, but do NOT enable or start, the personal WhatsApp user service.
set -eu
cd "$(dirname "$0")"
ROOT="$(pwd -P)"
case "$ROOT" in *'
'*|*'"'*) echo "Unsupported path characters" >&2; exit 1;; esac
NODE="$(command -v node)"
mkdir -p "$HOME/.config/systemd/user"
UNIT="$HOME/.config/systemd/user/open-ci-hub-personal-whatsapp.service"
if [ -e "$UNIT" ]; then
  echo "Service already exists; refusing to overwrite: $UNIT" >&2
  exit 1
fi
cat > "$UNIT" <<EOF
[Unit]
Description=Owner-approved personal WhatsApp terminal companion (unofficial)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=$NODE $ROOT/index.js
Restart=on-failure
RestartSec=25
NoNewPrivileges=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectControlGroups=true
LimitNOFILE=4096
MemoryMax=1536M
CPUQuota=75%
Environment=PUPPETEER_SKIP_DOWNLOAD=1

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user disable open-ci-hub-personal-whatsapp.service >/dev/null 2>&1 || true
echo "Service installed but NOT enabled/started."
echo "First pair the account interactively using npm start; approve contacts and style examples."
