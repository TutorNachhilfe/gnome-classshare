#!/usr/bin/env python3

import io
import socket
import subprocess
import threading
from datetime import datetime
from email.message import Message
from email.parser import BytesParser
from email.policy import default as email_default_policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

try:
    import qrcode
except ImportError:  # pragma: no cover
    qrcode = None

MAX_UPLOAD_SIZE_BYTES = 100 * 1024 * 1024
CONTENT_TOO_LARGE = getattr(HTTPStatus, "CONTENT_TOO_LARGE", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def safe_unique_path(directory: Path, filename: str) -> Path:
    path_obj = Path(filename)
    base = path_obj.name
    stem = path_obj.stem
    suffix = path_obj.suffix
    target = directory / base
    counter = 2
    while target.exists():
        target = directory / f"{stem}_{counter}{suffix}"
        counter += 1
    return target


class ClassShareState:
    def __init__(self):
        self.selected_file = None
        self.collecting_active = False
        self.upload_dir = Path.home() / "Abgaben"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.received = []
        self.server_port = None
        self.server_ip = get_local_ip()


class ClassShareHandler(BaseHTTPRequestHandler):
    state = None
    on_upload = None
    max_upload_size = MAX_UPLOAD_SIZE_BYTES

    def log_message(self, fmt, *args):
        return

    def _send_html(self, html: str, status=HTTPStatus.OK):
        data = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/download":
            self._handle_download()
            return
        if path == "/":
            self._handle_upload_page()
            return
        self._send_html("<h1>Nicht gefunden</h1>", status=HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/upload":
            self._handle_upload()
            return
        self._send_html("<h1>Nicht gefunden</h1>", status=HTTPStatus.NOT_FOUND)

    def _handle_download(self):
        selected = self.state.selected_file
        if not selected or not Path(selected).exists():
            self._send_html("<h1>Keine Datei verfügbar</h1>", status=HTTPStatus.NOT_FOUND)
            return

        file_path = Path(selected)
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", f'attachment; filename="{file_path.name}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_upload_page(self):
        if not self.state.collecting_active:
            self._send_html(
                """
                <!doctype html>
                <html lang="de"><meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <title>Abgabe pausiert</title>
                <body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; padding: 2rem;">
                <h1>Abgabe ist aktuell nicht aktiv.</h1>
                </body></html>
                """,
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return

        self._send_html(
            """
            <!doctype html>
            <html lang="de">
            <head>
              <meta charset="utf-8">
              <meta name="viewport" content="width=device-width, initial-scale=1">
              <title>Aufgabe abgeben</title>
              <style>
                body {
                  margin: 0;
                  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                  background: #f5f6f8;
                  color: #1f2937;
                  min-height: 100vh;
                  display: grid;
                  place-items: center;
                }
                .card {
                  width: min(720px, calc(100vw - 2rem));
                  background: #fff;
                  border-radius: 20px;
                  padding: 1.5rem;
                  box-shadow: 0 10px 24px rgba(0,0,0,.08);
                }
                h1 { margin-top: 0; font-size: clamp(1.7rem, 3vw, 2.2rem); }
                p { font-size: 1.05rem; }
                input[type=file] {
                  width: 100%;
                  padding: 1rem;
                  border: 2px dashed #93a3b8;
                  border-radius: 14px;
                  font-size: 1rem;
                  background: #f9fafb;
                  margin-bottom: 1rem;
                }
                button {
                  width: 100%;
                  border: 0;
                  border-radius: 14px;
                  padding: 1rem;
                  font-size: 1.25rem;
                  font-weight: 700;
                  background: #2563eb;
                  color: white;
                }
              </style>
            </head>
            <body>
              <main class="card">
                <h1>Aufgabe abgeben</h1>
                <p>Datei auswählen und auf <strong>Abgeben</strong> tippen.</p>
                <form action="/upload" method="post" enctype="multipart/form-data">
                  <input type="file" name="file" required>
                  <button type="submit">Abgeben</button>
                </form>
              </main>
            </body>
            </html>
            """
        )

    def _handle_upload(self):
        if not self.state.collecting_active:
            self._send_html("<h1>Abgabe ist nicht aktiv</h1>", status=HTTPStatus.SERVICE_UNAVAILABLE)
            return

        content_type = self.headers.get("Content-Type", "")
        header = Message()
        header["content-type"] = content_type
        if header.get_content_type() != "multipart/form-data":
            self._send_html("<h1>Ungültige Anfrage</h1>", status=HTTPStatus.BAD_REQUEST)
            return

        boundary = header.get_param("boundary")
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_html("<h1>Ungültige Anfrage</h1>", status=HTTPStatus.BAD_REQUEST)
            return
        if not boundary or content_length <= 0:
            self._send_html("<h1>Ungültige Anfrage</h1>", status=HTTPStatus.BAD_REQUEST)
            return
        if content_length > self.max_upload_size:
            self._send_html("<h1>Datei ist zu groß (max. 100 MB)</h1>", status=CONTENT_TOO_LARGE)
            return

        body = self.rfile.read(content_length)
        mime_blob = (
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
            + body
        )
        message = BytesParser(policy=email_default_policy).parsebytes(mime_blob)

        uploaded_name = None
        uploaded_data = None
        for part in message.iter_parts():
            if part.get_param("name", header="content-disposition") != "file":
                continue
            uploaded_name = part.get_filename()
            uploaded_data = part.get_payload(decode=True) or b""
            break

        if not uploaded_name:
            self._send_html("<h1>Keine Datei ausgewählt</h1>", status=HTTPStatus.BAD_REQUEST)
            return

        target = safe_unique_path(self.state.upload_dir, uploaded_name)
        try:
            with open(target, "wb") as out:
                out.write(uploaded_data)
        except OSError:
            self._send_html("<h1>Datei konnte nicht gespeichert werden</h1>", status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.state.received.append((target.name, timestamp))

        if self.on_upload:
            GLib.idle_add(self.on_upload, target.name, timestamp)

        self._send_html(
            """
            <!doctype html>
            <html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Erfolgreich abgegeben</title>
            <body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; padding: 2rem; text-align:center;">
              <h1>✅ Erfolgreich abgegeben!</h1>
              <p>Die Datei wurde an die Lehrkraft übertragen.</p>
            </body></html>
            """
        )


class ClassShareWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.app = app
        self.state = app.state
        self.set_title("GNOME ClassShare")
        self.set_default_size(760, 640)

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_margin_top(12)
        root.set_margin_bottom(12)
        root.set_margin_start(12)
        root.set_margin_end(12)
        self.toast_overlay.set_child(root)

        top = Gtk.Box(spacing=6)
        self.send_btn = Gtk.ToggleButton(label="Senden")
        self.collect_btn = Gtk.ToggleButton(label="Einsammeln")
        self.send_btn.set_active(True)
        self.send_btn.connect("toggled", self._on_mode_toggled, "send")
        self.collect_btn.connect("toggled", self._on_mode_toggled, "collect")
        top.append(self.send_btn)
        top.append(self.collect_btn)
        root.append(top)

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        root.append(self.stack)

        self.stack.add_titled(self._build_send_page(), "send", "Senden")
        self.stack.add_titled(self._build_collect_page(), "collect", "Einsammeln")

        self._update_qr_images()

    def _build_send_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        self.selected_label = Gtk.Label(label="Keine Datei gewählt")
        self.selected_label.set_xalign(0)
        box.append(self.selected_label)

        file_btn = Gtk.Button(label="Datei wählen")
        file_btn.connect("clicked", self._choose_file)
        box.append(file_btn)

        self.send_qr = Gtk.Picture()
        box.append(self.send_qr)

        return box

    def _build_collect_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        self.collect_start_btn = Gtk.Button(label="Abgabe starten")
        self.collect_start_btn.add_css_class("suggested-action")
        self.collect_start_btn.connect("clicked", self._toggle_collecting)
        box.append(self.collect_start_btn)

        self.collect_qr = Gtk.Picture()
        box.append(self.collect_qr)

        self.listbox = Gtk.ListBox()
        self.listbox.add_css_class("boxed-list")
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_child(self.listbox)
        box.append(scrolled)

        return box

    def _on_mode_toggled(self, btn, mode):
        if not btn.get_active():
            return
        if mode == "send":
            self.collect_btn.set_active(False)
            self.stack.set_visible_child_name("send")
        else:
            self.send_btn.set_active(False)
            self.stack.set_visible_child_name("collect")
        self._update_qr_images()

    def _choose_file(self, _btn):
        dialog = Gtk.FileChooserNative.new(
            "Datei auswählen",
            self,
            Gtk.FileChooserAction.OPEN,
            "Auswählen",
            "Abbrechen",
        )
        dialog.connect("response", self._on_file_response)
        dialog.show()

    def _on_file_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()
            if file:
                path = file.get_path()
                self.state.selected_file = path
                self.selected_label.set_text(f"Ausgewählt: {Path(path).name}")
                self._update_qr_images()
        dialog.destroy()

    def _toggle_collecting(self, _btn):
        self.state.collecting_active = not self.state.collecting_active
        label = "Abgabe stoppen" if self.state.collecting_active else "Abgabe starten"
        self.collect_start_btn.set_label(label)
        self._update_qr_images()

    def _url_for(self, mode):
        host = self.state.server_ip
        port = self.state.server_port
        suffix = "/download" if mode == "send" else "/"
        return f"http://{host}:{port}{suffix}"

    def _set_qr(self, picture, url):
        if qrcode is None:
            picture.set_paintable(None)
            return

        img = qrcode.make(url)
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        loader = GdkPixbuf.PixbufLoader.new_with_type("png")
        loader.write(buffer.getvalue())
        loader.close()
        pixbuf = loader.get_pixbuf()
        texture = Gdk.Texture.new_for_pixbuf(pixbuf)
        picture.set_paintable(texture)

    def _update_qr_images(self):
        if self.state.server_port:
            if self.state.selected_file:
                self._set_qr(self.send_qr, self._url_for("send"))
            else:
                self.send_qr.set_paintable(None)
            if self.state.collecting_active:
                self._set_qr(self.collect_qr, self._url_for("collect"))
            else:
                self.collect_qr.set_paintable(None)

    def _open_received_file(self, _btn, filepath: Path):
        if not filepath.exists():
            self.toast_overlay.add_toast(Adw.Toast(title=f"Datei nicht gefunden: {filepath.name}"))
            return

        missing_openers = True
        last_error = None
        for opener in ("gio", "xdg-open"):
            try:
                command = [opener, "open", str(filepath)] if opener == "gio" else [opener, str(filepath)]
                subprocess.Popen(command)
                return
            except FileNotFoundError:
                continue
            except OSError as err:
                missing_openers = False
                last_error = err

        if missing_openers:
            message = "Weder gio noch xdg-open ist verfügbar"
        elif last_error:
            message = f"Konnte Datei nicht öffnen: {last_error.strerror or 'Unbekannter Fehler'}"
        else:
            message = "Konnte Datei nicht öffnen"
        self.toast_overlay.add_toast(Adw.Toast(title=message))

    def on_upload_received(self, name, timestamp):
        row = Adw.ActionRow(title=name, subtitle=f"Eingegangen um {timestamp}")
        filepath = self.state.upload_dir / name
        if filepath.exists():
            open_btn = Gtk.Button(icon_name="document-open-symbolic")
            open_btn.add_css_class("flat")
            open_btn.set_tooltip_text("Öffnen")
            open_btn.set_valign(Gtk.Align.CENTER)
            open_btn.connect("clicked", self._open_received_file, filepath)
            row.add_suffix(open_btn)
            row.set_activatable_widget(open_btn)
        self.listbox.append(row)
        self.toast_overlay.add_toast(Adw.Toast(title=f"📥 {name} eingegangen"))
        return False


class ClassShareApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.tutornachhilfe.ClassShare")
        self.state = ClassShareState()
        self.server = None
        self.server_thread = None

    def do_activate(self):
        self._start_server()
        self.win = ClassShareWindow(self)
        self.win.present()

    def do_shutdown(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        super().do_shutdown()

    def _start_server(self):
        if self.server:
            return

        ClassShareHandler.state = self.state
        ClassShareHandler.on_upload = self._forward_upload

        self.server = ThreadingHTTPServer(("0.0.0.0", 0), ClassShareHandler)
        self.state.server_port = self.server.server_port
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def _forward_upload(self, name, timestamp):
        if hasattr(self, "win"):
            return self.win.on_upload_received(name, timestamp)
        return False


def main():
    app = ClassShareApp()
    app.run([])


if __name__ == "__main__":
    main()
