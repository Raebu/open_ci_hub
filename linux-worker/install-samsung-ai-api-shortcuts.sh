#!/usr/bin/env bash
# Install manual API-console launchers for Samsung Galaxy Book Go. No account login,
# API key storage, background browser or external API calls.
set -Eeuo pipefail
test "$(id -u)" -eq 0
test "$(hostname)" = samsung-book-go
test "$(uname -m)" = aarch64
test "$(id -u linux)" -eq 1000
D=/home/linux/.local/share/applications
install -d -o linux -g linux -m 0755 "$D"

add() {
  local id="$1" title="$2" url="$3" dest="$D/$1.desktop" tmp
  test ! -L "$dest" || { echo "Refusing symlinked shortcut: $id" >&2; exit 2; }
  tmp="$(mktemp "$D/.oci-api.XXXXXXXX")"
  cat > "$tmp" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=$title
Comment=Manually open developer API key console, no automatic requests or billing
Exec=firefox --new-window $url
Terminal=false
Icon=applications-internet
Categories=Network;Development;
StartupNotify=true
EOF
  chmod 0644 "$tmp"
  chown linux:linux "$tmp"
  mv -- "$tmp" "$dest"
}
add open-ci-gemini-api 'Open CI Hub · Gemini API Keys' 'https://aistudio.google.com/apikey'
add open-ci-nvidia-api 'Open CI Hub · NVIDIA NIM Catalog' 'https://build.nvidia.com/'
add open-ci-openrouter-api 'Open CI Hub · OpenRouter API Keys' 'https://openrouter.ai/settings/keys'
add open-ci-groq-api 'Open CI Hub · Groq API Keys' 'https://console.groq.com/keys'
for key in open-ci-gemini-api open-ci-nvidia-api open-ci-openrouter-api open-ci-groq-api; do
  test -f "$D/$key.desktop"
  stat -c '%n %U %a' "$D/$key.desktop"
done
echo SAMSUNG_API_CONSOLE_SHORTCUTS_INSTALLED
