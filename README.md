# gnome-classshare

GNOME-App zum Teilen und Einsammeln von Dateien per QR-Code im lokalen Netzwerk.

## Start

```bash
python3 gnome-classshare.py
```

## Modi

### Senden
1. **Datei wählen**
2. QR-Code wird angezeigt
3. Schüler scannt den Code und Safari lädt die Datei direkt herunter

### Einsammeln
1. **Einsammeln** öffnen
2. **Abgabe starten** klicken
3. Schüler scannt den QR-Code und sieht die Seite **„Aufgabe abgeben“**
4. Datei auswählen + **Abgeben**
5. Datei landet automatisch in `~/Abgaben/`

## Upload-Verhalten

- Upload per `POST /upload` (`multipart/form-data`)
- Max. Upload-Größe: 100 MB pro Datei
- Original-Dateiname bleibt erhalten
- Bei Namenskonflikten wird nummeriert, z. B. `hausaufgabe_2.pdf`
- Neue Uploads erscheinen sofort in der Liste im App-Fenster
- Pro neuer Datei erscheint ein Toast: `📥 <datei> eingegangen`

## Hinweis zum Netzwerk

Der integrierte HTTP-Server bindet an alle lokalen Interfaces (`0.0.0.0`), damit iPads im selben Netzwerk zugreifen können.
