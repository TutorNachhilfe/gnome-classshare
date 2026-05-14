#!/usr/bin/env python3

import argparse
import errno
import json
import logging
import shutil
import subprocess
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gio, Gtk, Pango  # noqa: E402

from constants import APP_DESKTOP_ID, CONFIG_DIR, SERVER_PORT, SETTINGS_FILE
from desktop_integration import ensure_desktop_file, install_icon
from handler import ClassShareHandler
from qr_utils import make_qr_texture
from state import ClassShareState
from utils import safe_unique_path, sanitize_filename, strip_timestamp_prefix, timestamp_prefix


class ClassShareWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.app = app
        self.state = app.state
        self._is_fullscreen = False

        self.set_title("ClassShare")
        self.set_size_request(700, 520)

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        toolbar = Adw.ToolbarView()
        self.toast_overlay.set_child(toolbar)
        toolbar.add_top_bar(self._build_header())

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        toolbar.set_content(content)

        self.server_error_revealer = Gtk.Revealer()
        self.server_error_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.server_error_label = Gtk.Label(label="")
        self.server_error_label.set_xalign(0)
        self.server_error_label.set_wrap(True)
        self.server_error_label.set_selectable(True)
        try:
            self.server_error_label.add_css_class("error")
        except AttributeError as exc:
            logging.debug("GTK-CSS-Klasse 'error' wird nicht unterstützt: %s", exc)
        server_error_frame = Gtk.Frame()
        server_error_frame.set_child(self.server_error_label)
        self.server_error_revealer.set_child(server_error_frame)
        content.append(self.server_error_revealer)

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        root.set_hexpand(True)
        root.set_vexpand(True)
        content.append(root)

        # Left side: send controls + student overview
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        left_box.set_hexpand(True)
        left_box.set_vexpand(True)
        root.append(left_box)

        left_box.append(self._build_send_controls())
        left_box.append(self._build_tutor_overview())

        # Right side: QR code panel
        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        right_box.set_hexpand(False)
        right_box.set_vexpand(False)
        right_box.set_valign(Gtk.Align.START)
        right_box.set_size_request(220, -1)
        root.append(right_box)

        qr_title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        qr_title_label = Gtk.Label(label="Schüler-Seite")
        qr_title_label.set_xalign(0)
        qr_title_label.set_hexpand(True)
        qr_title_row.append(qr_title_label)

        qr_fullscreen_btn = Gtk.Button(label="⛶ Vollbild")
        qr_fullscreen_btn.add_css_class("flat")
        qr_fullscreen_btn.connect("clicked", self._show_qr_fullscreen)
        qr_title_row.append(qr_fullscreen_btn)
        right_box.append(qr_title_row)

        self.qr_picture = Gtk.Picture()
        self.qr_picture.set_size_request(250, 250)
        right_box.append(self.qr_picture)

        self.ip_label = Gtk.Label(label="")
        self.ip_label.set_selectable(True)
        self.ip_label.set_xalign(0.5)
        self.ip_label.set_wrap(True)
        self.ip_label.add_css_class("caption")
        right_box.append(self.ip_label)
        right_box.append(self._build_settings())

        self._setup_actions()
        self._load_settings()
        self.connect("close-request", self._on_close_request)
        self._update_qr()
        self._refresh_selected_files_ui()
        self.refresh_from_state()

    def _build_header(self):
        header = Adw.HeaderBar()
        menu = Gio.Menu()
        menu.append("Über ClassShare", "win.show-about")
        menu.append("Tastenkürzel", "win.show-shortcuts")

        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        menu_btn.set_menu_model(menu)
        header.pack_start(menu_btn)

        self._fullscreen_btn = Gtk.Button(icon_name="view-fullscreen-symbolic")
        self._fullscreen_btn.connect("clicked", self._toggle_fullscreen)
        header.pack_end(self._fullscreen_btn)
        return header

    def _setup_actions(self):
        actions = [
            ("toggle-fullscreen", self._toggle_fullscreen),
            ("open-file", self._choose_file),
            ("show-about", self._show_about),
            ("show-shortcuts", self._show_shortcuts),
            ("close", self._close_window),
        ]
        for name, callback in actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        app = self.get_application()
        if app:
            app.set_accels_for_action("win.toggle-fullscreen", ["F11"])
            app.set_accels_for_action("win.open-file", ["<Primary>o"])
            app.set_accels_for_action("win.show-shortcuts", ["<Primary>question"])
            app.set_accels_for_action("win.close", ["<Primary>w"])
            app.set_accels_for_action("win.show-about", ["<Primary>F1"])

    def _build_send_controls(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        target_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        target_label = Gtk.Label(label="Empfänger")
        target_label.set_xalign(0)
        target_row.append(target_label)

        self._target_model = Gtk.StringList.new(["Alle Online-Schüler", "Alle Schüler"])
        self.target_combo = Gtk.DropDown.new(self._target_model, None)
        self.target_combo.set_selected(0)
        target_row.append(self.target_combo)
        box.append(target_row)

        self.selected_count_label = Gtk.Label(label="0 Datei(en) ausgewählt")
        self.selected_count_label.set_xalign(0)
        box.append(self.selected_count_label)

        file_btn = Gtk.Button(label="Datei wählen")
        file_btn.connect("clicked", self._choose_file)
        box.append(file_btn)

        self.selected_files_listbox = Gtk.ListBox()
        self.selected_files_listbox.add_css_class("boxed-list")
        self.selected_files_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        files_scrolled = Gtk.ScrolledWindow()
        files_scrolled.set_min_content_height(120)
        files_scrolled.set_child(self.selected_files_listbox)
        box.append(files_scrolled)

        send_btn = Gtk.Button(label="An Schüler senden")
        send_btn.add_css_class("suggested-action")
        send_btn.connect("clicked", self._send_files_to_students)
        box.append(send_btn)

        return box

    def _build_tutor_overview(self):
        frame = Gtk.Frame(label="Schüler-Übersicht")
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        content.set_margin_top(8)
        content.set_margin_bottom(8)
        content.set_margin_start(8)
        content.set_margin_end(8)
        frame.set_child(content)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for title, width in (("●", 30), ("Name", 190), ("Dateien erhalten", 140), ("Dateien gesendet", 140), ("Zuletzt aktiv", 180)):
            label = Gtk.Label(label=title)
            label.set_xalign(0)
            label.set_width_chars(max(4, width // 10))
            header.append(label)
        content.append(header)

        self.overview_list = Gtk.ListBox()
        self.overview_list.add_css_class("boxed-list")
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_child(self.overview_list)
        content.append(scrolled)

        return frame

    def _build_settings(self):
        frame = Gtk.Frame(label="Einstellungen")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        frame.set_child(box)

        name_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        name_label = Gtk.Label(label="App-Name")
        name_label.set_xalign(0)
        name_row.append(name_label)
        self.app_name_entry = Gtk.Entry()
        self.app_name_entry.set_text("ClassShare")
        self.app_name_entry.set_placeholder_text("ClassShare")
        self.app_name_entry.set_hexpand(True)
        self.app_name_entry.connect("changed", self._on_app_name_changed)
        name_row.append(self.app_name_entry)
        box.append(name_row)

        logo_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        logo_label = Gtk.Label(label="Logo")
        logo_label.set_xalign(0)
        logo_row.append(logo_label)
        self.logo_path_label = Gtk.Label(label="Standard")
        self.logo_path_label.set_hexpand(True)
        self.logo_path_label.set_xalign(0)
        self.logo_path_label.set_ellipsize(Pango.EllipsizeMode.START)
        logo_row.append(self.logo_path_label)
        logo_btn = Gtk.Button(label="Durchsuchen")
        logo_btn.add_css_class("flat")
        logo_btn.connect("clicked", self._choose_logo)
        logo_row.append(logo_btn)
        box.append(logo_row)

        return frame

    def _on_app_name_changed(self, entry):
        name = entry.get_text().strip() or "ClassShare"
        self.state.app_name = name
        self.set_title(name)
        self._save_settings()

    def _choose_logo(self, *_):
        dialog = Gtk.FileDialog.new()
        dialog.set_title("Logo auswählen")
        filter_img = Gtk.FileFilter()
        filter_img.set_name("Bilder (PNG, JPG, SVG)")
        filter_img.add_mime_type("image/png")
        filter_img.add_mime_type("image/jpeg")
        filter_img.add_mime_type("image/svg+xml")
        filter_store = Gio.ListStore.new(Gtk.FileFilter)
        filter_store.append(filter_img)
        dialog.set_filters(filter_store)
        dialog.open(self, None, self._on_logo_response)

    def _on_logo_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                path = file.get_path()
                if path:
                    self.state.logo_path = path
                    self.logo_path_label.set_text(Path(path).name)
                    self._save_settings()
        except GLib.Error as exc:
            if self._is_dismissed_dialog_error(exc):
                return
            logging.warning("Fehler beim Logo-Dialog: %s", exc)

    def _toggle_fullscreen(self, *_):
        self._is_fullscreen = not self._is_fullscreen
        if self._is_fullscreen:
            self.fullscreen()
            self._fullscreen_btn.set_icon_name("view-restore-symbolic")
        else:
            self.unfullscreen()
            self._fullscreen_btn.set_icon_name("view-fullscreen-symbolic")

    def _show_qr_fullscreen(self, *_):
        url = self._url_for_students() if self.state.server_port else ""
        fullscreen_texture = make_qr_texture(url) if url else None
        if url and fullscreen_texture is None:
            self.toast_overlay.add_toast(Adw.Toast(title="qrcode nicht installiert (pip install qrcode[pil])"))
            return

        win = Adw.Window()
        win.set_title(f"{self.state.app_name} QR-Code")
        win.set_transient_for(self)
        win.set_modal(False)
        win.set_default_size(420, 480)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_vexpand(True)
        outer.set_hexpand(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(32)
        box.set_margin_bottom(32)
        box.set_margin_start(32)
        box.set_margin_end(32)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        box.set_vexpand(True)
        box.set_hexpand(True)

        qr_pic = Gtk.Picture()
        qr_pic.set_size_request(360, 360)
        qr_pic.set_can_shrink(False)
        if fullscreen_texture is not None:
            qr_pic.set_paintable(fullscreen_texture)
        box.append(qr_pic)

        ip_lbl = Gtk.Label(label=url)
        ip_lbl.set_selectable(True)
        try:
            ip_lbl.add_css_class("title-2")
        except AttributeError as exc:
            logging.debug("GTK-CSS-Klasse 'title-2' wird nicht unterstützt: %s", exc)
        box.append(ip_lbl)

        hint = Gtk.Label(label="Klick oder Escape zum Schließen")
        try:
            hint.add_css_class("caption")
        except AttributeError as exc:
            logging.debug("GTK-CSS-Klasse 'caption' wird nicht unterstützt: %s", exc)
        box.append(hint)

        outer.append(box)

        click_ctrl = Gtk.GestureClick()
        click_ctrl.connect("pressed", lambda *_a: win.close())
        outer.add_controller(click_ctrl)

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", lambda _ctrl, keyval, *_a: win.close() if keyval == Gdk.KEY_Escape else None)
        win.add_controller(key_ctrl)

        win.set_content(outer)
        win.present()

    def _close_window(self, *_):
        self.close()

    def _show_about(self, *_):
        try:
            dialog = Adw.AboutDialog(
                application_name=self.state.app_name,
                version="1.0",
                comments="Dateien teilen und einsammeln im Schulnetz",
                license_type=Gtk.License.GPL_3_0,
                developers=["GitHub Copilot (KI)"],
                website="https://github.com/TutorNachhilfe/gnome-classshare",
                issue_url="https://github.com/TutorNachhilfe/gnome-classshare/issues",
            )
            dialog.present(self)
        except AttributeError as exc:
            logging.debug("About-Dialog wird in dieser GTK/Adw-Version nicht unterstützt: %s", exc)

    def _show_shortcuts(self, *_):
        try:
            ui_file = Path(__file__).parent / "shortcuts.ui"
            builder = Gtk.Builder.new_from_file(str(ui_file))
            win = builder.get_object("win")
            win.set_transient_for(self)
            win.present()
        except Exception as exc:
            logging.warning("Tastenkürzel-Fenster konnte nicht geöffnet werden: %s", exc)

    def _set_selected_files(self, files):
        deduplicated = []
        seen = set()
        for selected in files:
            if not selected:
                continue
            text = str(selected)
            if text in seen:
                continue
            seen.add(text)
            deduplicated.append(text)
        self.state.selected_files = deduplicated
        self._refresh_selected_files_ui()

    def _refresh_selected_files_ui(self):
        count = len(self.state.selected_files)
        self.selected_count_label.set_text(f"{count} Datei(en) ausgewählt")

        child = self.selected_files_listbox.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.selected_files_listbox.remove(child)
            child = nxt

        for file_path in self.state.selected_files:
            row = Gtk.ListBoxRow()
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row_box.set_margin_top(6)
            row_box.set_margin_bottom(6)
            row_box.set_margin_start(8)
            row_box.set_margin_end(8)

            label = Gtk.Label(label=Path(file_path).name)
            label.set_xalign(0)
            label.set_hexpand(True)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_tooltip_text(file_path)
            row_box.append(label)

            remove_btn = Gtk.Button(label="✕")
            remove_btn.add_css_class("flat")
            remove_btn.connect("clicked", self._remove_selected_file, file_path)
            row_box.append(remove_btn)

            row.set_child(row_box)
            self.selected_files_listbox.append(row)

    def _remove_selected_file(self, _btn, file_path):
        self._set_selected_files([path for path in self.state.selected_files if path != file_path])

    def _choose_file(self, *_):
        dialog = Gtk.FileDialog.new()
        dialog.set_title("Datei auswählen")
        dialog.open_multiple(self, None, self._on_file_response)

    def _on_file_response(self, dialog, result):
        try:
            files = dialog.open_multiple_finish(result)
            if files:
                paths = [files.get_item(i).get_path() for i in range(files.get_n_items()) if files.get_item(i).get_path()]
                if paths:
                    self._set_selected_files(paths)
                    self.toast_overlay.add_toast(Adw.Toast(title=f"📂 {len(paths)} Datei(en) ausgewählt"))
        except GLib.Error as exc:
            if self._is_dismissed_dialog_error(exc):
                return
            logging.warning("Fehler beim Datei-Dialog: %s", exc)

    def _send_files_to_students(self, _btn):
        selected = list(self.state.selected_files)
        if not selected:
            self.toast_overlay.add_toast(Adw.Toast(title="Keine Datei ausgewählt"))
            return

        target_text = self._selected_target_text()
        with self.state.lock:
            all_students = self.state.student_names()
            if target_text == "Alle Online-Schüler":
                target_students = [name for name in all_students if self.state.ws_connections.get(name)]
            elif target_text == "Alle Schüler":
                target_students = all_students
            else:
                target_students = [target_text]

        if not target_students:
            if target_text == "Alle Online-Schüler":
                self.toast_overlay.add_toast(Adw.Toast(title="Keine Online-Schüler verbunden"))
            else:
                self.toast_overlay.add_toast(Adw.Toast(title="Noch keine Schüler vorhanden"))
            return

        copied_files = 0
        with self.state.lock:
            for student in target_students:
                self.state.ensure_student_dirs(student)
                _, received_dir, _ = self.state.student_paths(student)
                for source_path in selected:
                    source = Path(source_path)
                    if not source.exists() or not source.is_file():
                        continue
                    filename = sanitize_filename(source.name)
                    target_name = f"{timestamp_prefix()}__{filename}"
                    destination = safe_unique_path(received_dir, target_name)
                    shutil.copy2(source, destination)
                    copied_files += 1
                    self.state.push_new_file(student, strip_timestamp_prefix(destination.name), destination.stat().st_size)

        self.refresh_from_state()
        self.toast_overlay.add_toast(Adw.Toast(title=f"📤 {copied_files} Datei(en) gesendet"))

    def _url_for_students(self):
        return f"http://{self.state.server_ip}:{self.state.server_port}/"

    def _set_qr(self, picture, url):
        texture = make_qr_texture(url)
        picture.set_paintable(texture)

    def _update_qr(self):
        if self.state.server_port:
            url = self._url_for_students()
            self._set_qr(self.qr_picture, url)
            self.ip_label.set_text(url)
            return
        self.qr_picture.set_paintable(None)
        self.ip_label.set_text("")

    def set_server_error(self, message: str | None):
        self.server_error_label.set_text(message or "")
        self.server_error_revealer.set_reveal_child(bool(message))

    def refresh_from_state(self):
        self._refresh_target_combo()
        self._refresh_overview_rows()
        return False

    def _refresh_target_combo(self):
        previous = self._selected_target_text()
        names = self.state.student_names()
        self._target_model = Gtk.StringList.new(["Alle Online-Schüler", "Alle Schüler"] + names)
        self.target_combo.set_model(self._target_model)
        if previous == "Alle Schüler":
            self.target_combo.set_selected(1)
        elif previous in names:
            self.target_combo.set_selected(names.index(previous) + 2)
        else:
            self.target_combo.set_selected(0)

    def _selected_target_text(self):
        selected_index = self.target_combo.get_selected()
        if selected_index == Gtk.INVALID_LIST_POSITION:
            return "Alle Online-Schüler"
        return self._target_model.get_string(selected_index) or "Alle Online-Schüler"

    def _is_dismissed_dialog_error(self, exc):
        dialog_error_quark = getattr(Gtk, "dialog_error_quark", None)
        dialog_error = getattr(Gtk, "DialogError", None)
        if dialog_error_quark is None or dialog_error is None:
            return True
        return exc.matches(dialog_error_quark(), dialog_error.DISMISSED)

    def _refresh_overview_rows(self):
        child = self.overview_list.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.overview_list.remove(child)
            child = nxt

        for entry in self.state.tutor_overview_rows():
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            outer_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            outer_box.set_margin_top(6)
            outer_box.set_margin_bottom(6)
            outer_box.set_margin_start(8)
            outer_box.set_margin_end(8)

            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            dot = Gtk.Label(label="🟢" if entry["online"] else "⚪")
            dot.set_width_chars(2)
            row_box.append(dot)

            name = Gtk.Label(label=entry["name"])
            name.set_xalign(0)
            name.set_width_chars(18)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            row_box.append(name)

            received = Gtk.Label(label=str(entry["received"]))
            received.set_xalign(0)
            received.set_width_chars(14)
            row_box.append(received)

            sent = Gtk.Label(label=str(entry["sent"]))
            sent.set_xalign(0)
            sent.set_width_chars(14)
            row_box.append(sent)

            last_active = Gtk.Label(label=entry["last_active"])
            last_active.set_xalign(0)
            last_active.set_width_chars(18)
            row_box.append(last_active)

            outer_box.append(row_box)

            sent_files = entry.get("sent_files", [])
            if sent_files:
                expander = Gtk.Expander(label=f"📨 {len(sent_files)} {'Datei' if len(sent_files) == 1 else 'Dateien'} eingereicht")
                files_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                files_box.set_margin_top(4)
                files_box.set_margin_start(8)
                for f in sent_files:
                    file_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                    file_row.set_margin_top(2)
                    file_row.set_margin_bottom(2)

                    fname_label = Gtk.Label(label=f["filename"])
                    fname_label.set_xalign(0)
                    fname_label.set_hexpand(True)
                    fname_label.set_ellipsize(Pango.EllipsizeMode.END)
                    fname_label.set_tooltip_text(f["path"])
                    file_row.append(fname_label)

                    view_btn = Gtk.Button(label="Anzeigen")
                    view_btn.add_css_class("flat")
                    view_btn.connect("clicked", self._open_file, f["path"])
                    file_row.append(view_btn)

                    dl_btn = Gtk.Button(label="Herunterladen")
                    dl_btn.add_css_class("flat")
                    dl_btn.connect("clicked", self._open_folder, f["folder"])
                    file_row.append(dl_btn)

                    files_box.append(file_row)
                expander.set_child(files_box)
                outer_box.append(expander)

            row.set_child(outer_box)
            self.overview_list.append(row)

    def _open_file(self, _btn, path: str):
        try:
            subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            logging.warning("Datei konnte nicht geöffnet werden (%s): %s", path, exc)
            self.toast_overlay.add_toast(Adw.Toast(title=f"Konnte Datei nicht öffnen: {Path(path).name}"))

    def _open_folder(self, _btn, folder: str):
        try:
            subprocess.Popen(["xdg-open", folder])
        except Exception as exc:
            logging.warning("Ordner konnte nicht geöffnet werden (%s): %s", folder, exc)
            self.toast_overlay.add_toast(Adw.Toast(title="Konnte Ordner nicht öffnen"))

    def on_student_upload(self, student: str, filename: str, _size: int):
        self.toast_overlay.add_toast(Adw.Toast(title=f"📥 {student}: {filename}"))
        self.refresh_from_state()
        return False

    def _load_settings(self):
        try:
            if SETTINGS_FILE.exists():
                data = json.loads(SETTINGS_FILE.read_text())
                self.set_default_size(data.get("width", 900), data.get("height", 740))
                app_name = data.get("app_name", "ClassShare") or "ClassShare"
                self.state.app_name = app_name
                self.app_name_entry.set_text(app_name)
                self.set_title(app_name)
                logo_path = data.get("logo_path")
                if logo_path and Path(logo_path).is_file():
                    self.state.logo_path = logo_path
                    self.logo_path_label.set_text(Path(logo_path).name)
                return
        except Exception as exc:
            logging.warning("Einstellungen konnten nicht geladen werden: %s", exc)
        self.set_default_size(900, 740)

    def _save_settings(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "width": self.get_width(),
                "height": self.get_height(),
                "app_name": self.state.app_name,
                "logo_path": self.state.logo_path,
            }
            SETTINGS_FILE.write_text(json.dumps(data))
        except Exception as exc:
            logging.warning("Einstellungen konnten nicht gespeichert werden: %s", exc)

    def _on_close_request(self, *_):
        self._save_settings()
        return False


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
        super().do_shutdown()

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
