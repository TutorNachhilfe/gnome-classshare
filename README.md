# gnome-classshare

GNOME-App zum Teilen und Einsammeln von Dateien per QR-Code im lokalen Netzwerk.

## Start

```bash
python3 gnome-classshare.py
```

Die Schüler-Seite läuft standardmäßig auf `http://<IP>:8080/`.
Optional kann ein anderer Port gesetzt werden:

```bash
python3 gnome-classshare.py --port 9090
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
