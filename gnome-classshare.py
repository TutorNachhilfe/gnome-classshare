#!/usr/bin/env python3
import mimetypes
import os
import socket
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote

import gi
import qrcode
from PIL import Image

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, Gtk, Pango  # noqa: E402


class SingleFileHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, file_path=None, route_name=None, **kwargs):
        self.file_path = file_path
        self.route_name = route_name
        super().__init__(*args, **kwargs)

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        requested = unquote(self.path.split("?", 1)[0]).lstrip("/")
        if requested != self.route_name:
            self.send_error(404, "Not Found")
            return

        try:
            file_size = os.path.getsize(self.file_path)
            content_type = mimetypes.guess_type(self.file_path)[0] or "application/octet-stream"
            file_name = os.path.basename(self.file_path).replace('"', "")

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(file_size))
            self.send_header("Content-Disposition", f'attachment; filename="{file_name}"')
            self.end_headers()

            with open(self.file_path, "rb") as src:
                self.wfile.write(src.read())
        except OSError:
            self.send_error(500, "File read failed")


class ClassShareWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="GNOME ClassShare")
        self.set_default_size(900, 650)

        self.server = None
        self.server_thread = None
        self.temp_dir = None
        self.qr_path = None
        self.current_file = None

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        toolbar_view = Adw.ToolbarView()
        self.toast_overlay.set_child(toolbar_view)

        header_bar = Adw.HeaderBar()
        header_bar.set_title_widget(Gtk.Label(label="GNOME ClassShare"))
        toolbar_view.add_top_bar(header_bar)

        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.main_box.set_margin_top(24)
        self.main_box.set_margin_bottom(24)
        self.main_box.set_margin_start(24)
        self.main_box.set_margin_end(24)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.main_box)
        toolbar_view.set_content(scroller)

        self._build_drop_zone()
        self._build_result_area()

        self.connect("close-request", self._on_close_request)

    def _build_drop_zone(self):
        self.drop_frame = Gtk.Frame()
        self.drop_frame.add_css_class("card")
        self.drop_frame.set_hexpand(True)
        self.drop_frame.set_vexpand(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(32)
        box.set_margin_bottom(32)
        box.set_margin_start(24)
        box.set_margin_end(24)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)

        icon = Gtk.Image.new_from_icon_name("document-send-symbolic")
        icon.set_pixel_size(72)

        title = Gtk.Label(label="Datei hierhin ziehen")
        title.add_css_class("title-2")

        subtitle = Gtk.Label(label="oder per Button auswählen")
        subtitle.add_css_class("dim-label")

        self.select_button = Gtk.Button(label="Datei auswählen")
        self.select_button.add_css_class("suggested-action")
        self.select_button.connect("clicked", self._on_select_clicked)

        box.append(icon)
        box.append(title)
        box.append(subtitle)
        box.append(self.select_button)

        self.drop_frame.set_child(box)
        self.main_box.append(self.drop_frame)

        drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop_target.connect("drop", self._on_drop)
        self.drop_frame.add_controller(drop_target)

    def _build_result_area(self):
        self.result_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.result_box.set_visible(False)

        self.qr_picture = Gtk.Picture()
        self.qr_picture.set_can_shrink(False)
        self.qr_picture.set_size_request(360, 360)
        self.qr_picture.set_halign(Gtk.Align.CENTER)

        self.url_label = Gtk.Label()
        self.url_label.set_selectable(True)
        self.url_label.set_wrap(True)
        self.url_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.url_label.set_halign(Gtk.Align.CENTER)

        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        button_row.set_halign(Gtk.Align.CENTER)

        new_file_btn = Gtk.Button(label="Neue Datei")
        new_file_btn.connect("clicked", self._on_select_clicked)

        stop_btn = Gtk.Button(label="Server stoppen")
        stop_btn.connect("clicked", self._on_stop_clicked)

        button_row.append(new_file_btn)
        button_row.append(stop_btn)

        self.result_box.append(self.qr_picture)
        self.result_box.append(self.url_label)
        self.result_box.append(button_row)

        self.main_box.append(self.result_box)

    def _on_select_clicked(self, _button):
        chooser = Gtk.FileChooserNative.new(
            "Datei auswählen",
            self,
            Gtk.FileChooserAction.OPEN,
            "Öffnen",
            "Abbrechen",
        )
        chooser.connect("response", self._on_file_chooser_response)
        chooser.show()

    def _on_file_chooser_response(self, chooser, response_id):
        if response_id == Gtk.ResponseType.ACCEPT:
            gio_file = chooser.get_file()
            if gio_file:
                path = gio_file.get_path()
                if path:
                    self._share_file(path)

    def _on_drop(self, _target, value, _x, _y):
        files = value.get_files()
        first = files.get_item(0) if files and files.get_n_items() > 0 else None
        if not first:
            self._toast("Keine Datei im Drop erkannt")
            return False

        path = first.get_path()
        if not path or not os.path.isfile(path):
            self._toast("Nur lokale Dateien werden unterstützt")
            return False

        self._share_file(path)
        return True

    def _share_file(self, file_path):
        if not os.path.isfile(file_path):
            self._toast("Datei nicht gefunden")
            return

        self._stop_server()
        self._cleanup_temp()

        ip = self._detect_lan_ip()
        if not ip:
            self._toast("Kein LAN-Netzwerk gefunden")
            return

        port = self._find_free_port(8080, ip)
        route_name = os.path.basename(file_path)
        quoted_route = quote(route_name)
        url = f"http://{ip}:{port}/{quoted_route}"

        handler = partial(SingleFileHandler, file_path=file_path, route_name=route_name)

        try:
            self.server = ThreadingHTTPServer((ip, port), handler)
        except OSError as exc:
            self._toast(f"Serverstart fehlgeschlagen: {exc}")
            return

        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

        self.current_file = file_path
        self._generate_qr(url)
        self.url_label.set_label(url)
        self.result_box.set_visible(True)

    def _generate_qr(self, url):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="gnome-classshare-")
        self.qr_path = os.path.join(self.temp_dir.name, "share-qr.png")

        qr_image = qrcode.make(url)
        qr_image = qr_image.convert("RGB")
        qr_image = qr_image.resize((360, 360), Image.Resampling.LANCZOS)
        qr_image.save(self.qr_path, "PNG")

        self.qr_picture.set_filename(self.qr_path)

    def _on_stop_clicked(self, _button):
        self._stop_server()
        self.result_box.set_visible(False)

    def _stop_server(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=1)
        self.server_thread = None

    def _cleanup_temp(self):
        if self.temp_dir:
            self.temp_dir.cleanup()
            self.temp_dir = None
            self.qr_path = None

    def _find_free_port(self, start_port, host):
        port = start_port
        while port <= 65535:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind((host, port))
                    return port
                except OSError:
                    port += 1
        raise RuntimeError("Kein freier Port gefunden")

    def _detect_lan_ip(self):
        candidates = []

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                ip = sock.getsockname()[0]
                if not ip.startswith("127."):
                    return ip
        except OSError:
            pass

        hostname = socket.gethostname()
        try:
            for ip in socket.gethostbyname_ex(hostname)[2]:
                if not ip.startswith("127."):
                    candidates.append(ip)
        except OSError:
            pass

        return candidates[0] if candidates else None

    def _toast(self, message):
        self.toast_overlay.add_toast(Adw.Toast.new(message))

    def _on_close_request(self, *_args):
        self._stop_server()
        self._cleanup_temp()
        return False


class ClassShareApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.gnome.ClassShare", flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        window = self.props.active_window
        if not window:
            window = ClassShareWindow(self)
        window.present()


def main():
    app = ClassShareApp()
    app.run(None)


if __name__ == "__main__":
    main()
