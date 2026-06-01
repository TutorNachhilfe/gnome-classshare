#!/usr/bin/env bash
# install.sh – ClassShare-Installationsskript
# Unterstützte Distributionen: Arch/Manjaro, Debian/Ubuntu, Fedora, openSUSE
# Läuft als normaler User; sudo nur wo nötig.

# ──────────────────────────────────────────────
# Farben & Hilfsfunktionen
# ──────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
err()  { echo -e "${RED}✗${NC} $*"; }
info() { echo -e "  $*"; }
step() { echo -e "\n${BOLD}$*${NC}"; }

# ──────────────────────────────────────────────
# Schritt 1/6 – Distributionserkennung
# ──────────────────────────────────────────────
step "[1/6] Distribution erkennen …"

PKG_MANAGER=""
INSTALL_CMD=""

if command -v pacman &>/dev/null; then
    PKG_MANAGER="pacman"
    INSTALL_CMD="sudo pacman -S --needed --noconfirm"
    PACKAGES=(python-gobject gtk4 libadwaita python-pillow python-qrcode avahi nss-mdns)
    ok "Arch Linux / Manjaro erkannt (pacman)"
elif command -v apt &>/dev/null; then
    PKG_MANAGER="apt"
    INSTALL_CMD="sudo apt-get install -y"
    PACKAGES=(python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 python3-pil python3-qrcode avahi-daemon libnss-mdns)
    ok "Debian / Ubuntu erkannt (apt)"
elif command -v dnf &>/dev/null; then
    PKG_MANAGER="dnf"
    INSTALL_CMD="sudo dnf install -y"
    PACKAGES=(python3-gobject gtk4 libadwaita python3-pillow python3-qrcode avahi nss-mdns)
    ok "Fedora erkannt (dnf)"
elif command -v zypper &>/dev/null; then
    PKG_MANAGER="zypper"
    INSTALL_CMD="sudo zypper install -y"
    PACKAGES=(python3-gobject gtk4 libadwaita python3-Pillow python3-qrcode avahi nss-mdns)
    ok "openSUSE erkannt (zypper)"
else
    warn "Kein unterstützter Paketmanager gefunden."
    warn "Bitte installiere die Abhängigkeiten manuell:"
    info "  python3-gobject, gtk4, libadwaita, python3-pillow, python3-qrcode, avahi, nss-mdns"
    PKG_MANAGER="unknown"
fi

# ──────────────────────────────────────────────
# Schritt 2/6 – Systempakete installieren
# ──────────────────────────────────────────────
step "[2/6] Systempakete installieren …"

if [[ "$PKG_MANAGER" != "unknown" ]]; then
    for pkg in "${PACKAGES[@]}"; do
        info "  → $pkg"
    done
    if $INSTALL_CMD "${PACKAGES[@]}"; then
        ok "Pakete installiert"
    else
        warn "Einige Pakete konnten nicht installiert werden – Installation wird fortgesetzt"
    fi
else
    warn "Paketinstallation übersprungen"
fi

# ──────────────────────────────────────────────
# Schritt 3/6 – mDNS einrichten
# ──────────────────────────────────────────────
step "[3/6] mDNS (Avahi) einrichten …"

# Avahi aktivieren
if systemctl is-enabled avahi-daemon &>/dev/null; then
    ok "Avahi-Dienst ist bereits aktiviert"
elif sudo systemctl enable --now avahi-daemon &>/dev/null; then
    ok "Avahi-Dienst aktiviert und gestartet"
else
    warn "Avahi konnte nicht aktiviert werden (systemd nicht verfügbar oder Fehler)"
fi

# nsswitch.conf anpassen – mdns4_hostname + mdns4 vor 'resolve' einfügen
NSSWITCH="/etc/nsswitch.conf"
if [[ -f "$NSSWITCH" ]]; then
    if grep -q "mdns4_hostname" "$NSSWITCH"; then
        ok "nsswitch.conf enthält bereits mdns4_hostname – keine Änderung nötig"
    else
        # mdns4_hostname und mdns4 vor 'resolve' einfügen (falls vorhanden),
        # sonst direkt nach 'hosts:' am Anfang
        if grep -q '\bresolve\b' "$NSSWITCH"; then
            sudo sed -i 's/\(^hosts:.*\)\bresolve\b/\1mdns4_hostname mdns4 resolve/' "$NSSWITCH"
        else
            sudo sed -i 's/^\(hosts:[[:space:]]*\)/\1mdns4_hostname mdns4 /' "$NSSWITCH"
        fi
        if grep -q "mdns4_hostname" "$NSSWITCH"; then
            ok "nsswitch.conf angepasst (mdns4_hostname und mdns4 ergänzt)"
        else
            warn "nsswitch.conf konnte nicht angepasst werden"
        fi
    fi
else
    warn "$NSSWITCH nicht gefunden – mDNS-Namensauflösung nicht konfiguriert"
fi

# ──────────────────────────────────────────────
# Schritt 4/6 – App-Dateien installieren
# ──────────────────────────────────────────────
step "[4/6] App-Dateien installieren …"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.local/share/gnome-classshare"

mkdir -p "$INSTALL_DIR"

# Dateien kopieren (alles außer .git und temporäre Dateien)
if command -v rsync &>/dev/null; then
    rsync -a --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
          --exclude='.gitignore' \
          "$SCRIPT_DIR/" "$INSTALL_DIR/"
else
    # rsync nicht verfügbar: selektiv kopieren via find
    find "$SCRIPT_DIR" -mindepth 1 -maxdepth 1 \
        ! -name '.git' ! -name '__pycache__' ! -name '.gitignore' \
        -exec cp -r {} "$INSTALL_DIR/" \;
fi

ok "App-Dateien installiert nach $INSTALL_DIR"

# ──────────────────────────────────────────────
# Schritt 5/6 – Icon installieren
# ──────────────────────────────────────────────
step "[5/6] Icon installieren …"

ICON_DIR="$HOME/.local/share/icons/hicolor"
SVG_ICON="$SCRIPT_DIR/icons/classshare.svg"
PNG_ICON="$INSTALL_DIR/assets/icon.png"

# SVG-Icon in den Icon-Theme-Ordner installieren (für GNOME-Startmenü)
if [[ -f "$SVG_ICON" ]]; then
    mkdir -p "$ICON_DIR/scalable/apps"
    cp "$SVG_ICON" "$ICON_DIR/scalable/apps/gnome-classshare.svg"
    ok "SVG-Icon installiert"
else
    warn "SVG-Icon nicht gefunden: $SVG_ICON"
fi

# PNG-Icon für die App selbst erzeugen (falls noch nicht vorhanden)
if [[ ! -f "$PNG_ICON" ]]; then
    mkdir -p "$(dirname "$PNG_ICON")"
    if python3 "$INSTALL_DIR/assets/create_icon.py" "$PNG_ICON" 2>/dev/null; then
        ok "Platzhalter-Icon erzeugt: $PNG_ICON"
    else
        warn "PNG-Icon konnte nicht erzeugt werden (Pillow fehlt?)"
    fi
else
    ok "Icon bereits vorhanden: $PNG_ICON"
fi

# Icon-Cache aktualisieren
if command -v gtk-update-icon-cache &>/dev/null; then
    gtk-update-icon-cache -f -t "$ICON_DIR" 2>/dev/null && ok "Icon-Cache aktualisiert" \
        || warn "Icon-Cache-Aktualisierung fehlgeschlagen (unkritisch)"
fi

# ──────────────────────────────────────────────
# Schritt 6/6 – Desktop-Eintrag erstellen
# ──────────────────────────────────────────────
step "[6/6] Desktop-Eintrag erstellen …"

DESKTOP_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$DESKTOP_DIR/gnome-classshare.desktop"

mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Name=ClassShare
GenericName=Datei-Sharing für den Unterricht
Comment=Dateien teilen und einsammeln im Schulnetz
Exec=python3 $INSTALL_DIR/app.py
Icon=gnome-classshare
Terminal=false
Type=Application
Categories=Education;Network;
StartupWMClass=ClassShare
StartupNotify=true
Keywords=Schule;Unterricht;Teilen;
EOF

ok "Desktop-Eintrag erstellt: $DESKTOP_FILE"

# Desktop-Datenbank aktualisieren
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null && ok "Desktop-Datenbank aktualisiert" \
        || warn "Desktop-Datenbank-Aktualisierung fehlgeschlagen (unkritisch)"
fi

# ──────────────────────────────────────────────
# Abschluss
# ──────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}✅ Installation abgeschlossen!${NC}"
echo ""
echo "Starte ClassShare über das Startmenü oder mit:"
echo "  python3 $INSTALL_DIR/app.py"
echo ""
echo "QR-Code-Adresse: http://tutor.local:8080/"
echo ""
echo "Hinweis: Melde dich ab und wieder an (oder starte den DE neu),"
echo "damit der Startmenüeintrag im Anwendungsmenü erscheint."
