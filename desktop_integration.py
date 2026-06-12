import logging
import shutil
import subprocess
import sys
from pathlib import Path


def install_icon() -> None:
    """Kopiert classshare.svg ins lokale Icon-Verzeichnis."""
    try:
        icon_src = Path(__file__).parent / "icons" / "classshare.svg"
        if not icon_src.exists():
            return
        icon_dir = Path.home() / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps"
        icon_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(icon_src, icon_dir / "gnome-classshare.svg")
        try:
            subprocess.Popen(
                ["gtk-update-icon-cache", "-f", "-t", str(Path.home() / ".local" / "share" / "icons" / "hicolor")],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            # Optionales Tool; fehlend ist unkritisch.
            logging.debug("gtk-update-icon-cache nicht verfügbar: %s", exc)
    except Exception as exc:
        logging.warning("Icon konnte nicht installiert werden: %s", exc)


def ensure_desktop_file(app_desktop_id: str) -> None:
    """Erstellt die .desktop-Datei falls sie noch nicht existiert."""
    try:
        desktop_dir = Path.home() / ".local" / "share" / "applications"
        desktop_dir.mkdir(parents=True, exist_ok=True)
        desktop_path = desktop_dir / app_desktop_id
        if not desktop_path.exists():
            exec_path = Path(__file__).parent / "app.py"
            desktop_path.write_text(
                "[Desktop Entry]\n"
                "Name=ClassShare\n"
                "Comment=Dateien teilen und einsammeln im Schulnetz\n"
                f"Exec={sys.executable} {exec_path}\n"
                "Icon=gnome-classshare\n"
                "Terminal=false\n"
                "Type=Application\n"
                "Categories=Education;Network;\n"
                "StartupWMClass=ClassShare\n"
            )
    except Exception as exc:
        logging.warning("Desktop-Datei konnte nicht erstellt werden: %s", exc)
