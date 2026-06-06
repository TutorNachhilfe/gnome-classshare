# gnome-classshare

GNOME-App zum Teilen und Einsammeln von Dateien per QR-Code im lokalen Netzwerk.

## Installation

```bash
git clone https://github.com/TutorNachhilfe/gnome-classshare.git
cd gnome-classshare
bash install.sh
```

Das Skript installiert alle Abhängigkeiten, richtet mDNS ein und erstellt einen Startmenüeintrag.
Unterstützte Distributionen: **Arch/Manjaro**, **Debian/Ubuntu**, **Fedora**, **openSUSE**.

### Manuelle Installation (paketbasiert)

<details>
<summary>Arch Linux (AUR)</summary>

```bash
git clone https://github.com/TutorNachhilfe/gnome-classshare.git
cd gnome-classshare
makepkg -si
```
</details>

<details>
<summary>Debian / Ubuntu</summary>

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 python3-pil
pip install qrcode
git clone https://github.com/TutorNachhilfe/gnome-classshare.git
cd gnome-classshare
sudo make install
```
</details>

<details>
<summary>Fedora</summary>

```bash
sudo dnf install python3-gobject gtk4 libadwaita python3-pillow
pip install qrcode
git clone https://github.com/TutorNachhilfe/gnome-classshare.git
cd gnome-classshare
sudo make install
```
</details>

### Direkt starten (ohne Installation)
```bash
pip install -r requirements.txt
python3 app.py
```

## Start

```bash
python3 app.py
```

Die Schüler-Seite läuft standardmäßig auf `http://<IP>:8080/`.
Optional kann ein anderer Port gesetzt werden:

```bash
python3 app.py --port 9090
```

## HTTPS (empfohlen)

Das Programm erkennt automatisch ob ein Let's Encrypt Zertifikat unter
`/etc/letsencrypt/live/local.tutor.schule/` vorhanden ist und aktiviert dann HTTPS.

Die Schüler-URL lautet dann: `https://local.tutor.schule:8080/`

Alternativ kann ein eigener Zertifikatspfad übergeben werden:

```bash
python3 app.py --cert /pfad/zu/fullchain.pem --key /pfad/zu/privkey.pem
```

## Ablauf

1. Tutor startet die App und zeigt den QR-Code (rechts im Fenster).
2. Schüler öffnen **eine gemeinsame Seite** (`/`) für Empfang + Upload.
3. Beim ersten Besuch geben Schüler ihren Namen ein – danach wird er im Browser gespeichert.
4. Beim nächsten Besuch (auch nach Neustart des Tutor-Programms) einfach denselben Namen eingeben und der alte Verlauf ist sofort wieder sichtbar.
5. Alle Daten liegen sortiert unter `~/ClassShare/<Name>/empfangen` und `~/ClassShare/<Name>/gesendet`.

## Schüler-Identifikation

- Name wird im **Browser-localStorage** gespeichert – kein Cookie, kein IP-Tracking.
- Erlaubte Zeichen: Buchstaben (inkl. Umlaute), Zahlen, Leerzeichen, Bindestrich.
- Ordner-Vergleich ist case-insensitive; originale Schreibweise bleibt erhalten.
- Bei Neustart des Tutor-Programms reicht es, denselben Namen erneut einzugeben.
- Abmelden löscht nur den Browser-localStorage-Eintrag; der Ordner bleibt erhalten.

## Tutor-Funktionen

- Mehrere Dateien auswählen
- Versand an **Alle Online-Schüler** (Standard), **Alle Schüler** oder gezielt an einen einzelnen Schüler
- Live-Übersicht mit:
  - Name
  - Dateien erhalten
  - Dateien gesendet
  - Zuletzt aktiv
  - Online-Status (WebSocket)

## Schüler-Seite

- Empfangene Dateien + gesendete Dateien direkt untereinander (ohne Abschnittstitel)
- Drag-&-Drop Uploadfeld + klassischer Dateiwähler
- Abmelden-Button
- Live-Benachrichtigung: `📄 Neue Datei von Tutor: ...`
- Automatisches Update der Dateiliste via WebSocket

## Sicherheit

- Downloads sind nur aus dem eigenen Schüler-Ordner erlaubt.
- Path-Traversal (`../`, `\`) wird beim Download blockiert.
- Dateinamen werden vor dem Speichern bereinigt.
- Upload-Dateien erhalten einen Timestamp-Prefix, um Kollisionen zu vermeiden.

## Dark/Light Mode

- Browser: automatische Anpassung über `prefers-color-scheme`
- GTK: folgt automatisch dem System (`Adw.ColorScheme.DEFAULT`)

## Projektstruktur

| Datei | Beschreibung |
|---|---|
| `app.py` | Einstiegspunkt – App-Initialisierung, Server-Start, Argumente |
| `window.py` | GTK4/Adwaita-Fenster (`ClassShareWindow`) inkl. UI-Logik |
| `handler.py` | HTTP-Request-Handler (GET, POST, WebSocket, Upload, Download) |
| `state.py` | Gemeinsamer Server-Zustand (Schüler, Dateien, WebSocket-Verbindungen) |
| `constants.py` | Konstanten (Ports, Limits, Pfade) |
| `utils.py` | Hilfsfunktionen (Dateinamen, Pfade, Timestamps) |
| `qr_utils.py` | QR-Code-Erzeugung als GTK-Texture |
| `desktop_integration.py` | Icon- und `.desktop`-Datei-Logik |
| `student.html` | Schüler-Oberfläche (HTML/CSS/JS-Template) |
| `shortcuts.ui` | GTK-Tastenkürzel-Fenster (GtkBuilder-XML) |
| `requirements.txt` | Python-Abhängigkeiten (ohne PyGObject) |
| `Makefile` | `make install` / `make uninstall` für alle Systeme |
| `PKGBUILD` | Arch Linux / AUR-Paketdefinition |
| `debian/` | Paketdefinition für Debian/Ubuntu |
| `gnome-classshare.spec` | RPM-Paketdefinition für Fedora/RHEL/openSUSE |
| `data/gnome-classshare.desktop` | Desktop-Eintrag für Anwendungsmenü |
| `data/classshare.sh` | Startskript für `/usr/bin/classshare` |
| `install.sh` | Installations-Skript für Arch, Debian/Ubuntu, Fedora, openSUSE |
| `assets/create_icon.py` | Erzeugt ein Platzhalter-PNG-Icon mit Pillow |
