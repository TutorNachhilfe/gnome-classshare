# gnome-classshare

GNOME-App zum Teilen und Einsammeln von Dateien per QR-Code im lokalen Netzwerk.

## Start

```bash
python3 gnome-classshare.py
```

## Ablauf

1. Tutor startet die App und zeigt den QR-Code.
2. Schüler öffnen **eine gemeinsame Seite** (`/`) für Empfang + Upload.
3. Beim ersten Besuch geben Schüler ihren Namen ein.
4. Danach erkennt der Server Schüler über Cookie + IP-Kombination.
5. Alle Daten liegen sortiert unter `~/ClassShare/<Name>/empfangen` und `~/ClassShare/<Name>/gesendet`.

## Schüler-Identifikation

- Cookie-Name: `classshare_name` (30 Tage)
- Reihenfolge:
  1. gültiger Cookie
  2. bekannte IP (setzt Cookie neu)
  3. sonst Namenseingabe
- Namen müssen eindeutig sein (case-insensitive).

## Tutor-Funktionen

- Mehrere Dateien auswählen
- Versand an **Alle Schüler** oder gezielt an einen einzelnen Schüler
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
