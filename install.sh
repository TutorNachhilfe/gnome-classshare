#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_SCRIPT="$ROOT_DIR/gnome-classshare.py"
DESKTOP_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$DESKTOP_DIR/gnome-classshare.desktop"

if command -v pip3 >/dev/null 2>&1; then
  PIP_CMD="pip3"
elif command -v pip >/dev/null 2>&1; then
  PIP_CMD="pip"
else
  echo "pip/pip3 nicht gefunden. Bitte Python-Paketmanager installieren." >&2
  exit 1
fi

"$PIP_CMD" install --user qrcode Pillow
mkdir -p "$DESKTOP_DIR"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=GNOME ClassShare
Comment=Dateien per QR-Code im WLAN teilen
Exec=python3 $APP_SCRIPT
Icon=org.gnome.FileManager
Terminal=false
Categories=GNOME;GTK;Education;Utility;
EOF

echo "Installation abgeschlossen: $DESKTOP_FILE"
