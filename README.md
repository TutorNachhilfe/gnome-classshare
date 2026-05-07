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
3. Schüler scannt den QR-Code und sieht die Seite **„Aufgabe abgeben"**
4. Datei auswählen + **Abgeben**
5. Datei landet automatisch in `~/Abgaben/`
6. In der Liste kann jede eingegangene Datei über **Öffnen** direkt mit der Standard-App geöffnet werden

## Upload-Verhalten

- Upload per `POST /upload` (`multipart/form-data`)
- Max. Upload-Größe: 100 MB pro Datei
- Original-Dateiname bleibt erhalten
- Bei Namenskonflikten wird nummeriert, z. B. `hausaufgabe_2.pdf`
- Neue Uploads erscheinen sofort in der Liste im App-Fenster
- Pro neuer Datei erscheint ein Toast: `�� <datei> eingegangen`

## Hinweis zum Netzwerk

Der integrierte HTTP-Server bindet an alle lokalen Interfaces (`0.0.0.0`), damit iPads im selben Netzwerk zugreifen können.
Nutze die App daher nur in vertrauenswürdigen (z. B. schulischen) Netzwerken, da keine Authentifizierung aktiviert ist.

## ⚠️ Sicherheitshinweise

Diese App startet einen ungeschützten HTTP-Server im lokalen Netzwerk. Folgende Punkte sollten bekannt sein:

- **Keine Authentifizierung:** Jeder im selben WLAN kann Dateien hoch- und herunterladen – nicht nur deine Schüler.
- **Kein HTTPS:** Die Übertragung ist unverschlüsselt. Für sensible Daten (z. B. Klausuren, Noten) nicht geeignet.
- **Dateinamen vom Schüler:** Der Original-Dateiname des Schülers wird übernommen. Schädliche Dateinamen werden zwar bereinigt, aber die App wurde nicht auf Sicherheit geprüft.
- **Kein Virenscan:** Hochgeladene Dateien werden nicht auf Schadsoftware geprüft.
- **Empfehlung:** Nur im Schulnetzwerk oder einem eigens dafür eingerichteten WLAN nutzen – niemals in einem öffentlichen Netzwerk.

## 🤖 Hinweis zur Entstehung

Diese App wurde vollständig von **GitHub Copilot (KI)** erstellt und wurde **nicht von einem Menschen überprüft oder getestet**. Der Code kann Fehler, Sicherheitslücken oder unerwartetes Verhalten enthalten. Nutzung auf eigene Verantwortung.

Erstellt im Mai 2026 als Unterrichtshilfsmittel für Lehrkräfte auf Linux/GNOME.
