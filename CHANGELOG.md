# Changelog

## [Unreleased]

### Hinzugefügt
- `requirements.txt` für einfache Installation
- `desktop_integration.py` – Icon- und Desktop-Datei-Logik ausgelagert
- `qr_utils.py` – QR-Code-Logik ausgelagert
- `shortcuts.ui` – GTK-Tastenkürzel-XML ausgelagert
- `student.html` – Schüler-Oberfläche ausgelagert

### Geändert
- Hauptdatei umbenannt von `gnome-classshare.py` zu `app.py`
- `server.py` Wrapper entfernt, direkte Imports stattdessen

### Behoben
- `on_state_change` / `on_student_upload` Callbacks wurden fälschlicherweise über `self` aufgerufen (TypeError bei Upload)
