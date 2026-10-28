#!/usr/bin/env bash
# Install user-clickable AI application menu shortcuts, no automatic browser launch.
set -Eeuo pipefail
test "$(id -u)" -eq 0
test "$(hostname)" = samsung-book-go
test "$(id -u linux)" = 1000
test -d /home/linux
install -d -o linux -g linux -m 0755 /home/linux/.local/share/applications
D=/home/linux/.local/share/applications
write_shortcut() {
  local name="$1" url="$2" title="$3"
  local tmp
  tmp="$(mktemp "$D/.oci-ai.XXXXXXXX")"
  cat > "$tmp" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=$title
Comment=Open only when selected; no background browser automation
Exec=firefox --new-window $url
Terminal=false
Icon=applications-internet
Categories=Network;Utility;
StartupNotify=true
EOF
  chmod 0644 "$tmp"
  chown linux:linux "$tmp"
  mv -f "$tmp" "$D/$name.desktop"
}
write_shortcut open-ci-qwen-local http://127.0.0.1:8878 'Open CI Hub · Local Qwen'
write_shortcut open-ci-huggingface-chat https://huggingface.co/chat/ 'Open CI Hub · Hugging Face Chat'
write_shortcut open-ci-google-gemini https://gemini.google.com/ 'Open CI Hub · Google Gemini'
for s in open-ci-qwen-local open-ci-huggingface-chat open-ci-google-gemini; do
  test -f "$D/$s.desktop"
  stat -c '%n owner=%U mode=%a' "$D/$s.desktop"
done
echo SAMSUNG_AI_SHORTCUTS_INSTALLED
