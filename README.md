# gnome-classshare

GNOME-App zum Teilen und Einsammeln von Dateien per QR-Code im lokalen Netzwerk.

## Installation

PyGObject muss über den Paketmanager installiert werden:

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1
```

Weitere Abhängigkeiten (z. B. QR-Code-Unterstützung) per pip:

```bash
pip install -r requirements.txt
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
| `app.py` | Hauptdatei – GTK4/Adwaita-Fenster, Einstellungen, Server-Start |
| `handler.py` | HTTP-Request-Handler (GET, POST, WebSocket, Upload, Download) |
| `state.py` | Gemeinsamer Server-Zustand (Schüler, Dateien, WebSocket-Verbindungen) |
| `constants.py` | Konstanten (Ports, Limits, Pfade) |
| `utils.py` | Hilfsfunktionen (Dateinamen, Pfade, Timestamps) |
| `qr_utils.py` | QR-Code-Erzeugung als GTK-Texture |
| `desktop_integration.py` | Icon- und `.desktop`-Datei-Logik |
| `student.html` | Schüler-Oberfläche (HTML/CSS/JS-Template) |
| `shortcuts.ui` | GTK-Tastenkürzel-Fenster (GtkBuilder-XML) |
| `requirements.txt` | Python-Abhängigkeiten (ohne PyGObject) |
