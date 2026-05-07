# GNOME ClassShare

**DE:** Native GNOME-App (GTK4 + Libadwaita), um Dateien per QR-Code im lokalen WLAN mit Schüler-iPads zu teilen – ohne zusätzliche App auf dem iPad.  
**EN:** Native GNOME app (GTK4 + Libadwaita) to share files to student iPads over local Wi-Fi via QR code, without installing an iPad app.

## Screenshot (Platzhalter / Placeholder)

![GNOME ClassShare Screenshot Placeholder](docs/screenshot-placeholder.png)

## Features

- Modernes Libadwaita-Fenster mit Headerbar
- Große Drag-&-Drop-Zone + „Datei auswählen"
- Automatischer lokaler HTTP-Downloadserver (Port 8080, sonst nächster freier Port)
- QR-Code-Anzeige (mind. 300px)
- URL-Anzeige zum Kopieren
- `Content-Disposition: attachment` für direkten Safari-Download
- „Neue Datei" und „Server stoppen"
- Sauberes Aufräumen bei App-Ende

## Installation

```bash
cd gnome-classshare
./install.sh
```

Das Script installiert `qrcode` und `Pillow` und legt einen Desktop-Eintrag unter:

`~/.local/share/applications/gnome-classshare.desktop`

Danach erscheint **GNOME ClassShare** im App-Drawer.

## Start

```bash
python3 gnome-classshare.py
```

## Wichtig / Important

Lehrergerät und Schüler-iPads müssen im **gleichen WLAN** sein.  
Teacher device and student iPads must be on the **same Wi-Fi network**.
