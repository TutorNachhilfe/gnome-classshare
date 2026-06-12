#!/usr/bin/env python3

import json
import logging
import shutil
from pathlib import Path
from typing import Optional

from gi.repository import Adw, Gdk, GLib, Gio, Gtk, Pango

from constants import CONFIG_DIR, CUSTOM_ICON_DIR, CUSTOM_ICON_PATH, SETTINGS_FILE
from qr_utils import make_qr_texture
from utils import encode_file_id, safe_unique_path, sanitize_filename, strip_timestamp_prefix, timestamp_prefix


class ClassShareWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.app = app
        self.state = app.state
        self._is_fullscreen = False
        self._loading_settings = False

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

        qr_fullscreen_btn = Gtk.Button.new_from_icon_name("view-fullscreen-symbolic")
        qr_fullscreen_btn.set_tooltip_text("QR-Code im Vollbild")
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

        self._fullscreen_btn = Gtk.Button.new_from_icon_name("view-fullscreen-symbolic")
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
        file_btn.set_icon_name("document-open-symbolic")
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
        send_btn.set_icon_name("document-send-symbolic")
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
        if not self._loading_settings:
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
                    try:
                        CUSTOM_ICON_DIR.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(path, CUSTOM_ICON_PATH)
                        persistent_path = str(CUSTOM_ICON_PATH)
                    except Exception as exc:
                        logging.warning("Icon konnte nicht in persistenten Pfad kopiert werden: %s", exc)
                        persistent_path = path
                    self.state.logo_path = persistent_path
                    self.logo_path_label.set_text(Path(persistent_path).name)
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
        url = self._url_for_students()
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

    def _clear_listbox(self, listbox):
        child = listbox.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            listbox.remove(child)
            child = nxt

    def _refresh_selected_files_ui(self):
        count = len(self.state.selected_files)
        self.selected_count_label.set_text(f"{count} Datei(en) ausgewählt")

        self._clear_listbox(self.selected_files_listbox)

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

            remove_btn = Gtk.Button.new_from_icon_name("edit-delete-symbolic")
            remove_btn.add_css_class("flat")
            remove_btn.set_tooltip_text("Entfernen")
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
                paths = []
                for index in range(files.get_n_items()):
                    file = files.get_item(index)
                    path = file.get_path() if file else None
                    if path:
                        paths.append(path)
                if paths:
                    self._set_selected_files(paths)
                    self.toast_overlay.add_toast(Adw.Toast(title=f"{len(paths)} Datei(en) ausgewählt"))
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
        self.toast_overlay.add_toast(Adw.Toast(title=f"{copied_files} Datei(en) gesendet"))

    def _url_for_students(self) -> str:
        if not self.state.server_port:
            return ""
        return f"http://{self.state.server_ip}:{self.state.server_port}/"

    def _annotate_url_for(self, ident: str, is_pdf: bool) -> str:
        key = "pdf" if is_pdf else "img"
        path = "annotate" if is_pdf else "annotate-img"
        return f"http://localhost:{self.state.server_port}/{path}?{key}={ident}"

    def _update_qr(self):
        if self.state.server_port:
            url = self._url_for_students()
            self.qr_picture.set_paintable(make_qr_texture(url))
            self.ip_label.set_text(url)
            return
        self.qr_picture.set_paintable(None)
        self.ip_label.set_text("")

    def set_server_error(self, message: Optional[str]) -> None:
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
        self._clear_listbox(self.overview_list)

        for entry in self.state.tutor_overview_rows():
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            outer_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            outer_box.set_margin_top(6)
            outer_box.set_margin_bottom(6)
            outer_box.set_margin_start(8)
            outer_box.set_margin_end(8)

            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            dot = Gtk.Label(label="●" if entry["online"] else "○")
            dot.set_width_chars(2)
            row_box.append(dot)

            name = Gtk.Label(label=entry["name"])
            name.set_xalign(0)
            name.set_width_chars(18)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            row_box.append(name)

            received = Gtk.Label(label=str(entry["received"]))
            received.set_xalign(1)
            received.set_width_chars(14)
            row_box.append(received)

            sent = Gtk.Label(label=str(entry["sent"]))
            sent.set_xalign(1)
            sent.set_width_chars(14)
            row_box.append(sent)

            last_active = Gtk.Label(label=entry["last_active"])
            last_active.set_xalign(1)
            last_active.set_width_chars(18)
            row_box.append(last_active)

            rename_btn = Gtk.Button.new_from_icon_name("document-edit-symbolic")
            rename_btn.add_css_class("flat")
            rename_btn.set_tooltip_text("Umbenennen")
            rename_btn.connect("clicked", lambda _b, n=entry["name"]: self._rename_student_dialog(n))
            row_box.append(rename_btn)

            del_btn = Gtk.Button.new_from_icon_name("edit-delete-symbolic")
            del_btn.add_css_class("flat")
            del_btn.set_tooltip_text("Schüler löschen")
            del_btn.connect("clicked", lambda _b, n=entry["name"]: self._delete_student_confirm(n))
            row_box.append(del_btn)

            outer_box.append(row_box)

            received_files = entry.get("received_files", [])
            received_expander = self._build_files_expander(received_files, entry["name"], "Erhalten:", "Datei empfangen", "Dateien empfangen")
            if received_expander:
                outer_box.append(received_expander)

            sent_files = entry.get("sent_files", [])
            sent_expander = self._build_files_expander(sent_files, entry["name"], "Gesendet:", "Datei eingereicht", "Dateien eingereicht")
            if sent_expander:
                outer_box.append(sent_expander)

            row.set_child(outer_box)
            self.overview_list.append(row)

    def _build_files_expander(self, files, student_name: str, label_prefix: str, singular: str, plural: str):
        if not files:
            return None
        expander = Gtk.Expander(label=f"{label_prefix} {len(files)} {singular if len(files) == 1 else plural}")
        files_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        files_box.set_margin_top(4)
        files_box.set_margin_start(8)
        for f in files:
            file_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            file_row.set_margin_top(2)
            file_row.set_margin_bottom(2)

            fname_label = Gtk.Label(label=f["filename"])
            fname_label.set_xalign(0)
            fname_label.set_hexpand(True)
            fname_label.set_width_chars(20)
            fname_label.set_ellipsize(Pango.EllipsizeMode.END)
            fname_label.set_tooltip_text(f["path"])
            file_row.append(fname_label)

            view_btn = Gtk.Button.new_from_icon_name("document-open-symbolic")
            view_btn.add_css_class("flat")
            view_btn.set_tooltip_text("Anzeigen")
            view_btn.connect("clicked", lambda _b, p=f["path"]: self._launch_file(p))
            file_row.append(view_btn)

            if self.state.server_port:
                fname_lower = f["filename"].lower()
                for exts, is_pdf, tip in (
                    ((".pdf",), True, "PDF annotieren"),
                    ((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"), False, "Bild annotieren"),
                ):
                    if fname_lower.endswith(exts):
                        ident = f.get("file_id") or encode_file_id(f.get("download") or f["path"])
                        ann_btn = Gtk.Button.new_from_icon_name("document-edit-symbolic")
                        ann_btn.add_css_class("flat")
                        ann_btn.set_tooltip_text(tip)
                        ann_url = self._annotate_url_for(ident, is_pdf)
                        ann_btn.connect("clicked", lambda _b, u=ann_url: self._launch_uri(u))
                        file_row.append(ann_btn)
                        break

            dl_btn = Gtk.Button.new_from_icon_name("document-save-symbolic")
            dl_btn.add_css_class("flat")
            dl_btn.set_tooltip_text("Ordner öffnen")
            dl_btn.connect("clicked", lambda _b, d=f["folder"]: self._launch_file(d))
            file_row.append(dl_btn)

            del_btn = Gtk.Button.new_from_icon_name("edit-delete-symbolic")
            del_btn.add_css_class("flat")
            del_btn.set_tooltip_text("Datei löschen")
            del_btn.connect("clicked", lambda _b, p=f["path"], n=student_name: self._delete_student_file(p, n))
            file_row.append(del_btn)

            files_box.append(file_row)
        expander.set_child(files_box)
        return expander

    def _rename_student_dialog(self, old_name: str):
        try:
            dialog = Gtk.AlertDialog()
            dialog.set_message(f"„{old_name}“ umbenennen")
            dialog.set_detail("Neuen Namen eingeben")
            dialog.set_buttons(["Abbrechen", "Umbenennen"])
            dialog.set_cancel_button(0)
            dialog.set_default_button(1)

            entry = Gtk.Entry()
            entry.set_text(old_name)
            entry.set_placeholder_text("Neuer Name")
            entry.set_activates_default(True)
            dialog.set_extra_child(entry)

            def on_response(dialog, result):
                try:
                    idx = dialog.choose_finish(result)
                    if idx == 1:
                        new_name = entry.get_text().strip()
                        if new_name and new_name != old_name:
                            with self.state.lock:
                                error = self.state.rename_student(old_name, new_name)
                            if error:
                                self.toast_overlay.add_toast(Adw.Toast(title=error))
                            else:
                                self.toast_overlay.add_toast(Adw.Toast(title=f"Umbenannt: {old_name} → {new_name}"))
                                self.refresh_from_state()
                    entry.unparent()
                except GLib.Error:
                    pass
            dialog.choose(self, None, on_response)
        except AttributeError:
            pass

    def _delete_student_confirm(self, name: str):
        try:
            dialog = Gtk.AlertDialog()
            dialog.set_message(f"„{name}“ löschen?")
            dialog.set_detail("Alle Dateien dieses Schülers werden unwiderruflich gelöscht.")
            dialog.set_buttons(["Abbrechen", "Löschen"])
            dialog.set_cancel_button(0)
            dialog.set_default_button(0)

            def on_response(dialog, result):
                try:
                    idx = dialog.choose_finish(result)
                    if idx == 1:
                        with self.state.lock:
                            error = self.state.delete_student(name)
                        if error:
                            self.toast_overlay.add_toast(Adw.Toast(title=error))
                        else:
                            self.toast_overlay.add_toast(Adw.Toast(title=f"Schüler gelöscht: {name}"))
                            self.refresh_from_state()
                except GLib.Error:
                    pass
            dialog.choose(self, None, on_response)
        except AttributeError:
            pass

    def _launch_file(self, path: str):
        try:
            Gtk.FileLauncher(file=Gio.File.new_for_path(path)).launch(self)
        except Exception as exc:
            logging.warning("Datei konnte nicht geöffnet werden (%s): %s", path, exc)
            self.toast_overlay.add_toast(Adw.Toast(title=f"Konnte nicht öffnen: {Path(path).name}"))

    def _launch_uri(self, uri: str):
        try:
            Gtk.UriLauncher(uri=uri).launch(self)
        except Exception as exc:
            logging.warning("URL konnte nicht geöffnet werden (%s): %s", uri, exc)
            self.toast_overlay.add_toast(Adw.Toast(title="Konnte URL nicht öffnen"))

    def _delete_student_file(self, file_path: str, student_name: str):
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError as exc:
            logging.warning("Datei konnte nicht gelöscht werden: %s", exc)
            self.toast_overlay.add_toast(Adw.Toast(title="Konnte Datei nicht löschen"))
            return
        with self.state.lock:
            self.state.push_file_list(student_name)
        self.refresh_from_state()
        self.toast_overlay.add_toast(Adw.Toast(title="Datei gelöscht"))

    def on_student_upload(self, student: str, filename: str, _size: int):
        self.toast_overlay.add_toast(Adw.Toast(title=f"{student}: {filename}"))
        self.refresh_from_state()
        return False

    def _load_settings(self):
        self._loading_settings = True
        try:
            try:
                if SETTINGS_FILE.exists():
                    data = json.loads(SETTINGS_FILE.read_text())
                    self.set_default_size(data.get("width", 900), data.get("height", 740))
                    app_name = data.get("app_name", "ClassShare") or "ClassShare"
                    self.state.app_name = app_name
                    self.app_name_entry.set_text(app_name)
                    self.set_title(app_name)
                    if CUSTOM_ICON_PATH.is_file():
                        logo_path = str(CUSTOM_ICON_PATH)
                    else:
                        logo_path = data.get("logo_path")
                    if logo_path and Path(logo_path).is_file():
                        self.state.logo_path = logo_path
                        self.logo_path_label.set_text(Path(logo_path).name)
                    return
            except Exception as exc:
                logging.warning("Einstellungen konnten nicht geladen werden: %s", exc)
            if CUSTOM_ICON_PATH.is_file():
                self.state.logo_path = str(CUSTOM_ICON_PATH)
                self.logo_path_label.set_text(CUSTOM_ICON_PATH.name)
            self.set_default_size(900, 740)
        finally:
            self._loading_settings = False

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
