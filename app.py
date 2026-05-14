#!/usr/bin/env python3

import argparse
import errno
import logging
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib  # noqa: E402

from constants import APP_DESKTOP_ID, SERVER_PORT
from desktop_integration import ensure_desktop_file, install_icon
from handler import ClassShareHandler
from state import ClassShareState
from window import ClassShareWindow


class ClassShareApp(Adw.Application):
    def __init__(self, server_port: int):
        super().__init__(application_id="com.tutornachhilfe.ClassShare")
        self.server_port = server_port
        self.state = ClassShareState()
        self.server = None
        self.server_thread = None
        self.win = None

    def do_activate(self):
        try:
            # DEFAULT uses the system preference with libadwaita, including auto dark/light switching.
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)
        except Exception as exc:
            logging.warning("Farbmodus konnte nicht auf Systemvorgabe gesetzt werden: %s", exc)

        # Nur beim direkten Start aus dem Quellverzeichnis, nicht bei systemweiter Installation
        _INSTALLED_DATA_DIR = Path("/usr/share/classshare")
        if not _INSTALLED_DATA_DIR.exists():
            ensure_desktop_file(APP_DESKTOP_ID)
            install_icon()

        if self.win is None:
            self.win = ClassShareWindow(self)
        self.win.present()

        if self.server:
            self.win.set_server_error(None)
            self.win._update_qr()
            return

        try:
            self._start_server()
        except OSError as exc:
            self.state.server_port = None
            self.win._update_qr()
            if exc.errno == errno.EADDRINUSE:
                self.win.set_server_error(f"Port {self.server_port} ist bereits belegt. Läuft das Programm schon?")
                return
            self.win.set_server_error(None)
            raise
        self.win.set_server_error(None)
        self.win._update_qr()

    def do_shutdown(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        Gio.Application.do_shutdown(self)

    def _start_server(self):
        if self.server:
            return

        ClassShareHandler.state = self.state
        ClassShareHandler.on_state_change = lambda: GLib.idle_add(self._forward_state_change)
        ClassShareHandler.on_student_upload = (
            lambda student_name, filename, size: GLib.idle_add(self._forward_student_upload, student_name, filename, size)
        )

        self.server = ThreadingHTTPServer(("0.0.0.0", self.server_port), ClassShareHandler)
        self.state.server_port = self.server.server_port
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def _forward_state_change(self):
        if self.win is not None:
            return self.win.refresh_from_state()
        return False

    def _forward_student_upload(self, student_name: str, filename: str, size: int):
        if self.win is not None:
            return self.win.on_student_upload(student_name, filename, size)
        return False


def _parse_args():
    parser = argparse.ArgumentParser(description="ClassShare Tutor-App")
    parser.add_argument(
        "--port",
        type=int,
        default=SERVER_PORT,
        help=f"Port für den integrierten HTTP-Server (Standard: {SERVER_PORT})",
    )
    args = parser.parse_args()
    if not (1 <= args.port <= 65535):
        parser.error("--port muss zwischen 1 und 65535 liegen")
    return args


def main():
    logging.basicConfig(level=logging.WARNING)
    args = _parse_args()
    app = ClassShareApp(server_port=args.port)
    app.run([])


if __name__ == "__main__":
    main()
