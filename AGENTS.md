# gnome-classshare

GNOME-App zum Teilen und Einsammeln von Dateien per QR-Code im lokalen Netzwerk.

## Tech Stack

- Python 3 mit GTK4 (PyGObject) und LibAdwaita 1
- QR-Codes via `qrcode[pil]` + `pillow`
- HTTP-Server: `http.server.ThreadingHTTPServer`
- Build: `python app.py`, Tests via `pytest`

## Projektstruktur

```
app.py                 # Einstiegspunkt – GTK-Fenster + HTTP-Server
window.py              # GTK4/Adwaita-UI
handler.py             # HTTP-Handler für Schüler-Dateien
state.py               # Gemeinsamer Zustand (IP, Port, Einstellungen)
constants.py           # App-Konstanten
qr_utils.py            # QR-Code-Erzeugung
desktop_integration.py # .desktop-Datei + Icon-Installation
utils.py               # Hilfsfunktionen
student.html           # Web-Oberfläche für Schüler
manifest.json           # PWA-Manifest (Installation auf Homescreen)
sw.js                   # Service Worker (Offline-Cache)
shortcuts.ui           # GTK4-Tastenkürzel
pdf_annotate/          # PDF-Annotation (viewer.html + routes)
tests/                 # pytest-Tests
```

## Wichtige Hinweise

- Kein HTTPS/TLS – nur unverschlüsseltes HTTP im LAN
- QR-Code zeigt `http://<lokale-ip>:8080/`
- Einstellungen unter `~/.config/gnome-classshare/settings.json`
- Installation via `bash install.sh` oder Distro-Pakete (AUR, .deb)
- UI-Code folgt GTK4/Adwaita-Mustern
- Python-Code: PEP 8
