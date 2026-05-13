#!/usr/bin/env python3

import base64
import errno
import hashlib
import io
import json
import re
import shutil
import socket
import subprocess
import sys
import threading
from datetime import datetime
from email.message import Message
from email.parser import BytesParser
from email.policy import default as email_default_policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GdkPixbuf, GLib, Gio, Gtk, Pango  # noqa: E402

try:
    import qrcode
except ImportError:  # pragma: no cover
    qrcode = None

MAX_UPLOAD_SIZE_BYTES = 100 * 1024 * 1024
CONTENT_TOO_LARGE = getattr(HTTPStatus, "CONTENT_TOO_LARGE", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
SERVER_PORT = 8080
WS_TIMEOUT_SECONDS = 30
CONFIG_DIR = Path.home() / ".config" / "gnome-classshare"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
APP_DESKTOP_ID = "gnome-classshare.desktop"
CLASSSHARE_ROOT = Path.home() / "ClassShare"


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{round(size_bytes / 1024)} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def sanitize_student_name(raw: str) -> str:
    normalized = (raw or "").strip()
    if not normalized:
        return ""
    # Allow letters (including German umlauts), numbers, spaces, hyphens
    normalized = re.sub(r"[^A-Za-z0-9äöüÄÖÜß -]", "", normalized)
    normalized = re.sub(r" +", " ", normalized)
    normalized = re.sub(r"-+", "-", normalized)
    normalized = normalized.strip(" -")
    return normalized[:64]


def sanitize_filename(raw: str) -> str:
    filename = Path((raw or "").replace("\x00", "")).name
    filename = re.sub(r"[\x00-\x1f\x7f]", "_", filename)
    filename = re.sub(r"[\\/\r\n\t]", "_", filename)
    filename = re.sub(r"[^A-Za-z0-9äöüÄÖÜß.,()\[\]{}+@=_ -]", "_", filename)
    filename = re.sub(r"\s+", " ", filename).strip(" .")
    return filename[:180] or "datei"


def timestamp_prefix() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def safe_unique_path(directory: Path, filename: str) -> Path:
    path_obj = Path(filename)
    stem = path_obj.stem
    suffix = path_obj.suffix
    target = directory / path_obj.name
    counter = 2
    while target.exists():
        target = directory / f"{stem}_{counter}{suffix}"
        counter += 1
    return target


def strip_timestamp_prefix(filename: str) -> str:
    if "__" in filename:
        return filename.split("__", 1)[1]
    return filename


def _ws_send_text(sock, text: str) -> bool:
    payload = text.encode("utf-8")
    frame = bytearray([0x81])
    size = len(payload)
    if size < 126:
        frame.append(size)
    elif size < 65536:
        frame.append(126)
        frame.extend(size.to_bytes(2, "big"))
    else:
        frame.append(127)
        frame.extend(size.to_bytes(8, "big"))
    frame.extend(payload)
    try:
        sock.sendall(frame)
        return True
    except OSError:
        return False


def _ws_send_json(sock, payload: dict) -> bool:
    return _ws_send_text(sock, json.dumps(payload, ensure_ascii=False))


def _ws_recv_frame(sock):
    head = sock.recv(2)
    if len(head) < 2:
        return None, b""
    first, second = head
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        ext = sock.recv(2)
        if len(ext) < 2:
            return None, b""
        length = int.from_bytes(ext, "big")
    elif length == 127:
        ext = sock.recv(8)
        if len(ext) < 8:
            return None, b""
        length = int.from_bytes(ext, "big")

    mask = b""
    if masked:
        mask = sock.recv(4)
        if len(mask) < 4:
            return None, b""

    payload = b""
    while len(payload) < length:
        chunk = sock.recv(length - len(payload))
        if not chunk:
            return None, b""
        payload += chunk

    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

    return opcode, payload


class ClassShareState:
    def __init__(self):
        self.server_port = None
        self.server_ip = get_local_ip()
        self.base_dir = CLASSSHARE_ROOT
        self.base_dir.mkdir(parents=True, exist_ok=True)

        self.ws_connections = {}
        self.last_active = {}
        self.selected_files = []
        self.lock = threading.RLock()

    def student_names(self):
        names = [p.name for p in self.base_dir.iterdir() if p.is_dir()]
        names.sort(key=lambda name: name.casefold())
        return names

    def resolve_name(self, name: str) -> str | None:
        lookup = {existing.casefold(): existing for existing in self.student_names()}
        return lookup.get(name.casefold())

    def ensure_student_dirs(self, name: str):
        student_dir = self.base_dir / name
        received_dir = student_dir / "empfangen"
        sent_dir = student_dir / "gesendet"
        received_dir.mkdir(parents=True, exist_ok=True)
        sent_dir.mkdir(parents=True, exist_ok=True)
        return student_dir, received_dir, sent_dir

    def student_paths(self, name: str):
        student_dir = self.base_dir / name
        return student_dir, student_dir / "empfangen", student_dir / "gesendet"

    def touch_active(self, name: str):
        self.last_active[name] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def file_list_payload(self, student_name: str):
        _, received_dir, sent_dir = self.student_paths(student_name)

        def read_dir(path: Path, scope: str):
            items = []
            if not path.exists():
                return items
            files = [f for f in path.iterdir() if f.is_file()]
            files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
            for file_path in files:
                stat = file_path.stat()
                items.append(
                    {
                        "filename": strip_timestamp_prefix(file_path.name),
                        "stored_name": file_path.name,
                        "size": stat.st_size,
                        "size_human": format_size(stat.st_size),
                        "mtime": stat.st_mtime,
                        "timestamp": datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m.%Y %H:%M"),
                        "download": f"/download?name={quote(student_name)}&scope={scope}&file={quote(file_path.name)}",
                        "view": f"/view?name={quote(student_name)}&scope={scope}&file={quote(file_path.name)}",
                    }
                )
            return items

        return {
            "type": "file_list",
            "received": read_dir(received_dir, "received"),
            "sent": read_dir(sent_dir, "sent"),
        }

    def tutor_overview_rows(self):
        rows = []
        names = self.student_names()
        for name in names:
            _, received_dir, sent_dir = self.student_paths(name)
            received_count = len([p for p in received_dir.glob("*") if p.is_file()]) if received_dir.exists() else 0
            sent_files_list = []
            if sent_dir.exists():
                sf = sorted([p for p in sent_dir.glob("*") if p.is_file()], key=lambda f: f.stat().st_mtime, reverse=True)
                for fp in sf:
                    sent_files_list.append({"filename": strip_timestamp_prefix(fp.name), "path": str(fp), "folder": str(fp.parent)})
            sent_count = len(sent_files_list)
            is_online = bool(self.ws_connections.get(name))
            rows.append(
                {
                    "name": name,
                    "received": received_count,
                    "sent": sent_count,
                    "sent_files": sent_files_list,
                    "last_active": self.last_active.get(name, "-"),
                    "online": is_online,
                }
            )
        return rows

    def sockets_for_name(self, student_name: str):
        sockets = self.ws_connections.get(student_name, set())
        return list(sockets)

    def add_socket(self, student_name: str, sock):
        if student_name not in self.ws_connections:
            self.ws_connections[student_name] = set()
        self.ws_connections[student_name].add(sock)

    def remove_socket(self, student_name: str, sock):
        sockets = self.ws_connections.get(student_name)
        if not sockets:
            return
        sockets.discard(sock)
        if not sockets:
            self.ws_connections.pop(student_name, None)

    def push_file_list(self, student_name: str):
        payload = self.file_list_payload(student_name)
        for sock in self.sockets_for_name(student_name):
            if not _ws_send_json(sock, payload):
                self.remove_socket(student_name, sock)

    def push_new_file(self, student_name: str, filename: str, size: int):
        payload = {
            "type": "new_file",
            "filename": filename,
            "size": size,
        }
        for sock in self.sockets_for_name(student_name):
            if not _ws_send_json(sock, payload):
                self.remove_socket(student_name, sock)
        self.push_file_list(student_name)



class ClassShareHandler(BaseHTTPRequestHandler):
    state = None
    on_state_change = None
    on_student_upload = None
    max_upload_size = MAX_UPLOAD_SIZE_BYTES

    def log_message(self, fmt, *args):
        return

    def _safe_header_value(self, value: str) -> str:
        return "".join(ch for ch in str(value) if ch not in "\r\n" and (32 <= ord(ch) <= 126))

    def _send_bytes(
        self,
        content: bytes,
        content_type: str,
        status=HTTPStatus.OK,
        *,
        content_disposition: str | None = None,
    ):
        self.send_response(status)
        self.send_header("Content-Type", self._safe_header_value(content_type))
        if content_disposition:
            self.send_header("Content-Disposition", self._safe_header_value(content_disposition))
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _send_html(self, html: str, status=HTTPStatus.OK):
        self._send_bytes(html.encode("utf-8"), "text/html; charset=utf-8", status=status)

    def _send_json(self, payload: dict, status=HTTPStatus.OK):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(data, "application/json; charset=utf-8", status=status)

    def _notify_state_change(self):
        if self.on_state_change:
            GLib.idle_add(self.on_state_change)

    def _name_from_query(self, query_string: str) -> str | None:
        """Extract and validate the student name from a URL query string."""
        params = parse_qs(query_string)
        raw = params.get("name", [""])[0]
        normalized = sanitize_student_name(raw)
        if not normalized:
            return None
        with self.state.lock:
            return self.state.resolve_name(normalized)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._handle_root()
            return
        if parsed.path == "/api/files":
            self._handle_file_list_api()
            return
        if parsed.path == "/download":
            self._handle_download(parsed)
            return
        if parsed.path == "/view":
            self._handle_view(parsed)
            return
        if parsed.path == "/ws":
            self._handle_websocket()
            return
        self._send_html("<h1>Nicht gefunden</h1>", status=HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/login":
            self._handle_api_login()
            return
        if path == "/upload":
            self._handle_upload()
            return
        self._send_html("<h1>Nicht gefunden</h1>", status=HTTPStatus.NOT_FOUND)

    def _handle_root(self):
        self._send_html(self._render_app_page())

    def _render_app_page(self):
        return """
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ClassShare</title>
  <style>
    :root {
      --bg: #ffffff;
      --card: #f5f5f5;
      --text: #1a1a1a;
      --accent: #0066cc;
      --border: #e0e0e0;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #1a1a1a;
        --card: #2d2d2d;
        --text: #f0f0f0;
        --accent: #4da6ff;
        --border: #444444;
      }
    }
    *, *::before, *::after { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }

    /* ── Login screen ── */
    #login-screen {
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 1rem;
    }
    .card {
      width: min(420px, 100%);
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 2rem;
    }
    .card h1 { margin: 0 0 1rem 0; font-size: clamp(1.8rem, 6vw, 2.5rem); }
    .name-input {
      width: 100%;
      padding: 1rem;
      font-size: clamp(1rem, 4vw, 1.4rem);
      border-radius: 14px;
      border: 1px solid var(--border);
      background: var(--bg);
      color: var(--text);
      margin-bottom: .75rem;
    }
    .login-btn {
      width: 100%;
      padding: 1rem;
      border: 0;
      border-radius: 14px;
      font-size: clamp(1rem, 4vw, 1.3rem);
      font-weight: 700;
      background: var(--accent);
      color: #fff;
      cursor: pointer;
    }
    .error { color: #e11d48; font-weight: 700; margin: .25rem 0 .75rem 0; font-size: .95rem; }

    /* ── Student screen ── */
    #student-screen {
      display: flex;
      flex-direction: column;
      height: 100dvh;
      overflow: hidden;
    }
    .wrap {
      display: flex;
      flex-direction: column;
      flex: 1;
      overflow: hidden;
      max-width: 800px;
      width: 100%;
      margin: 0 auto;
      padding: .75rem;
      gap: .75rem;
    }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: .75rem 1rem;
      flex-shrink: 0;
      gap: .75rem;
    }
    .brand { font-weight: 700; font-size: 1.05rem; }
    .who { display: flex; align-items: center; gap: .5rem; }
    .logout {
      border: 1px solid var(--border);
      background: transparent;
      color: var(--text);
      border-radius: 10px;
      padding: .35rem .55rem;
      cursor: pointer;
      font-size: .95rem;
    }
    .main-content {
      display: flex;
      flex-direction: column;
      flex: 1;
      overflow: hidden;
      gap: .75rem;
    }
    .dropzone-area {
      flex-shrink: 0;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: .8rem;
    }
    .drop {
      border: 2px dashed var(--border);
      border-radius: 12px;
      text-align: center;
      padding: 1.4rem .8rem;
      background: var(--bg);
      cursor: pointer;
    }
    .choose {
      margin-top: .7rem;
      border: 0;
      border-radius: 10px;
      padding: .55rem .8rem;
      background: var(--accent);
      color: #fff;
      font-weight: 600;
      cursor: pointer;
    }
    .file-list-area {
      flex: 1;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      min-height: 0;
    }
    .file-list-scroll {
      flex: 1;
      overflow-y: auto;
      -webkit-overflow-scrolling: touch;
      padding: .6rem;
      display: flex;
      flex-direction: column;
      gap: .45rem;
    }
    .row {
      display: flex;
      align-items: center;
      gap: .45rem;
      padding: .65rem .75rem;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: var(--bg);
      min-height: 48px;
    }
    .name {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      flex: 1;
      min-width: 0;
    }
    .meta {
      font-size: .84rem;
      opacity: .75;
      white-space: nowrap;
      flex-shrink: 0;
    }
    .btn {
      border: 0;
      background: var(--accent);
      color: #fff;
      border-radius: 8px;
      padding: .35rem .55rem;
      text-decoration: none;
      font-size: 1rem;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 44px;
      min-height: 44px;
      flex-shrink: 0;
    }
    .btn-ghost {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--text);
    }
    .load-more {
      width: 100%;
      padding: .75rem;
      border: 1px solid var(--border);
      background: var(--bg);
      color: var(--text);
      border-radius: 10px;
      cursor: pointer;
      font-size: .9rem;
      margin-top: .25rem;
    }
    #toast {
      position: fixed;
      left: 50%;
      transform: translateX(-50%);
      top: 1rem;
      background: var(--text);
      color: var(--bg);
      border-radius: 10px;
      padding: .65rem .9rem;
      display: none;
      z-index: 20;
      max-width: min(92vw, 560px);
    }
    @media (max-width: 480px) {
      .wrap { padding: .5rem; gap: .5rem; }
      .drop { padding: 1rem .6rem; }
      .meta { display: none; }
    }
    @media (min-width: 481px) and (max-width: 768px) {
      .wrap { padding: .6rem; gap: .6rem; }
    }
    @media (orientation: landscape) and (max-width: 1024px) {
      .main-content { flex-direction: row; }
      .dropzone-area { width: 40%; flex-shrink: 0; display: flex; flex-direction: column; justify-content: center; }
      .file-list-area { flex: 1; width: 60%; }
    }
    @media (min-width: 1025px) {
      .row { padding: .55rem .75rem; }
    }
  </style>
</head>
<body>

  <!-- Login screen -->
  <div id="login-screen">
    <main class="card">
      <h1>&#x1F464; Dein Name</h1>
      <p id="login-error" class="error" style="display:none"></p>
      <form id="login-form">
        <input id="name-input" class="name-input" type="text"
               autocomplete="name" placeholder="Vorname Nachname" required autofocus>
        <button class="login-btn" type="submit">Weiter &#x2192;</button>
      </form>
    </main>
  </div>

  <!-- Student screen -->
  <div id="student-screen" style="display:none">
    <div id="toast"></div>
    <div class="wrap">
      <div class="header">
        <div class="brand">&#x1F4DA; ClassShare</div>
        <div class="who">&#x1F464; <span id="current-name"></span>
          <button id="logout" class="logout">&#x21A9;&#xFE0F;</button></div>
      </div>
      <div class="main-content">
        <div class="dropzone-area">
          <div id="dropzone" class="drop">
            <div>&#x1F4E4; Datei hierher ziehen</div>
            <button id="choose" class="choose" type="button">oder Datei w&#xE4;hlen</button>
            <input id="file-input" type="file" name="files" multiple hidden>
          </div>
        </div>
        <div class="file-list-area">
          <div id="file-list" class="file-list-scroll"></div>
        </div>
      </div>
    </div>
  </div>

  <script>
    const STORAGE_KEY = 'classshare_name';

    const toast = document.getElementById('toast');
    const fileList = document.getElementById('file-list');
    const PAGE_SIZE = 5;
    let allFiles = [];
    let visibleCount = PAGE_SIZE;
    let currentName = '';
    let wsConn = null;

    function showToast(text) {
      toast.textContent = text;
      toast.style.display = 'block';
      clearTimeout(showToast._timer);
      showToast._timer = setTimeout(() => { toast.style.display = 'none'; }, 3200);
    }

    function showLoginScreen() {
      document.getElementById('login-screen').style.display = 'grid';
      document.getElementById('student-screen').style.display = 'none';
    }

    function showStudentScreen(name) {
      document.getElementById('login-screen').style.display = 'none';
      document.getElementById('student-screen').style.display = 'flex';
      document.getElementById('current-name').textContent = name;
      currentName = name;
      loadFiles();
      connectWebSocket();
    }

    // Check localStorage on load
    (function init() {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        showStudentScreen(stored);
      } else {
        showLoginScreen();
      }
    })();

    // Login
    document.getElementById('login-form').addEventListener('submit', async function(e) {
      e.preventDefault();
      const nameInput = document.getElementById('name-input');
      const errorEl = document.getElementById('login-error');
      const name = nameInput.value.trim();
      errorEl.style.display = 'none';
      try {
        const response = await fetch('/api/login', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({name: name})
        });
        const data = await response.json();
        if (data.ok) {
          localStorage.setItem(STORAGE_KEY, data.name);
          showStudentScreen(data.name);
        } else {
          errorEl.textContent = data.error || 'Fehler beim Anmelden';
          errorEl.style.display = 'block';
        }
      } catch (_) {
        errorEl.textContent = 'Verbindungsfehler';
        errorEl.style.display = 'block';
      }
    });

    // Logout
    document.getElementById('logout').addEventListener('click', function() {
      localStorage.removeItem(STORAGE_KEY);
      currentName = '';
      if (wsConn) { wsConn.close(); wsConn = null; }
      location.reload();
    });

    function renderRow(file) {
      const row = document.createElement('div');
      row.className = 'row';

      const icon = file.scope === 'received' ? '\\u{1F4E5}' : '\\u{1F4E4}';
      const ack = file.scope === 'sent' ? '\\u2705\\u00a0' : '';

      const nameDiv = document.createElement('div');
      nameDiv.className = 'name';
      nameDiv.textContent = icon + '\\u00a0' + file.filename;

      const metaDiv = document.createElement('div');
      metaDiv.className = 'meta';
      metaDiv.textContent = ack + file.size_human + ' \\u00b7 ' + file.timestamp;

      row.appendChild(nameDiv);
      row.appendChild(metaDiv);

      if (file.view) {
        const viewBtn = document.createElement('a');
        viewBtn.className = 'btn btn-ghost';
        viewBtn.href = file.view;
        viewBtn.target = '_blank';
        viewBtn.rel = 'noopener noreferrer';
        viewBtn.title = 'Anzeigen';
        viewBtn.textContent = '\\u{1F441}\\uFE0F';
        row.appendChild(viewBtn);
      }

      const dlBtn = document.createElement('a');
      dlBtn.className = 'btn';
      dlBtn.href = file.download;
      dlBtn.title = 'Herunterladen';
      dlBtn.textContent = '\\u2B07\\uFE0F';
      row.appendChild(dlBtn);

      return row;
    }

    function renderFiles() {
      fileList.replaceChildren();
      const visible = allFiles.slice(0, visibleCount);
      visible.forEach(function(file) { fileList.appendChild(renderRow(file)); });

      if (allFiles.length > visibleCount) {
        const remaining = Math.min(PAGE_SIZE, allFiles.length - visibleCount);
        const btn = document.createElement('button');
        btn.className = 'load-more';
        btn.textContent = remaining + ' weitere anzeigen';
        btn.onclick = function() {
          visibleCount += PAGE_SIZE;
          renderFiles();
        };
        fileList.appendChild(btn);
      }
    }

    function updateFiles(data) {
      allFiles = (data.received || [])
        .map(function(f) { return Object.assign({}, f, {scope: 'received'}); })
        .sort(function(a, b) { return (b.mtime || 0) - (a.mtime || 0); });
      renderFiles();
    }

    async function loadFiles() {
      if (!currentName) return;
      try {
        const response = await fetch('/api/files?name=' + encodeURIComponent(currentName), {cache: 'no-store'});
        if (!response.ok) return;
        updateFiles(await response.json());
      } catch (_) {}
    }

    async function uploadFiles(files) {
      if (!files || files.length === 0 || !currentName) return;
      const body = new FormData();
      for (const file of files) body.append('files', file);
      try {
        const response = await fetch('/upload?name=' + encodeURIComponent(currentName), {method: 'POST', body: body});
        if (!response.ok) {
          showToast('Upload fehlgeschlagen');
          return;
        }
        await loadFiles();
        showToast('\\u2705 Datei hochgeladen');
      } catch (_) {
        showToast('Upload fehlgeschlagen');
      }
    }

    const fileInput = document.getElementById('file-input');
    const dropzone = document.getElementById('dropzone');

    document.getElementById('choose').addEventListener('click', function() { fileInput.click(); });
    fileInput.addEventListener('change', function() { uploadFiles(fileInput.files); });

    dropzone.addEventListener('dragover', function(event) {
      event.preventDefault();
      dropzone.style.borderColor = 'var(--accent)';
    });
    dropzone.addEventListener('dragleave', function() {
      dropzone.style.borderColor = 'var(--border)';
    });
    dropzone.addEventListener('drop', function(event) {
      event.preventDefault();
      dropzone.style.borderColor = 'var(--border)';
      uploadFiles(event.dataTransfer.files);
    });

    function connectWebSocket() {
      if (!currentName) return;
      const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
      wsConn = new WebSocket(scheme + '://' + location.host + '/ws?name=' + encodeURIComponent(currentName));
      wsConn.onmessage = function(event) {
        try {
          const payload = JSON.parse(event.data);
          if (payload.type === 'new_file') {
            showToast('\\u{1F4C4} Neue Datei von Tutor: ' + payload.filename);
          }
          if (payload.type === 'file_list') {
            updateFiles(payload);
          }
        } catch (_) {}
      };
      wsConn.onclose = function() {
        wsConn = null;
        if (currentName) setTimeout(connectWebSocket, 1500);
      };
    }
  </script>
</body>
</html>
"""

    def _handle_api_login(self):
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length <= 0 or content_length > 16 * 1024:
            self._send_json({"error": "Ungültige Eingabe"}, status=HTTPStatus.BAD_REQUEST)
            return

        payload = self.rfile.read(content_length).decode("utf-8", errors="replace")
        try:
            data = json.loads(payload)
            submitted = data.get("name", "") if isinstance(data, dict) else ""
        except (json.JSONDecodeError, AttributeError):
            self._send_json({"error": "Ungültige Eingabe"}, status=HTTPStatus.BAD_REQUEST)
            return

        normalized = sanitize_student_name(submitted)
        if not normalized:
            self._send_json({"error": "Bitte einen gültigen Namen eingeben (Buchstaben, Zahlen, Leerzeichen, Bindestrich)"}, status=HTTPStatus.BAD_REQUEST)
            return

        with self.state.lock:
            existing = self.state.resolve_name(normalized)
            canonical = existing if existing else normalized
            self.state.ensure_student_dirs(canonical)
            self.state.touch_active(canonical)

        self._notify_state_change()
        self._send_json({"ok": True, "name": canonical})

    def _handle_file_list_api(self):
        student_name = self._name_from_query(urlparse(self.path).query)
        if not student_name:
            self._send_json({"error": "Unbekannter oder fehlender Name"}, status=HTTPStatus.BAD_REQUEST)
            return

        with self.state.lock:
            payload = self.state.file_list_payload(student_name)
        self._send_json(payload)

    def _resolve_requested_file(self, parsed_url):
        student_name = self._name_from_query(parsed_url.query)
        if not student_name:
            self._send_html("<h1>Nicht erlaubt</h1>", status=HTTPStatus.FORBIDDEN)
            return None

        params = parse_qs(parsed_url.query)
        scope = params.get("scope", [""])[0]
        requested = params.get("file", [""])[0]

        if not requested or requested != Path(requested).name or "\x00" in requested:
            self._send_html("<h1>Ungültiger Dateiname</h1>", status=HTTPStatus.BAD_REQUEST)
            return None

        _, received_dir, sent_dir = self.state.student_paths(student_name)
        if scope == "received":
            directory = received_dir
        elif scope == "sent":
            directory = sent_dir
        else:
            self._send_html("<h1>Ungültige Anfrage</h1>", status=HTTPStatus.BAD_REQUEST)
            return None

        file_path = directory / requested
        if not file_path.exists() or not file_path.is_file():
            self._send_html("<h1>Datei nicht gefunden</h1>", status=HTTPStatus.NOT_FOUND)
            return None
        return file_path

    def _handle_download(self, parsed_url):
        file_path = self._resolve_requested_file(parsed_url)
        if file_path is None:
            return

        data = file_path.read_bytes()
        download_name = sanitize_filename(strip_timestamp_prefix(file_path.name))
        disposition = f'attachment; filename="{download_name}"'
        self._send_bytes(data, "application/octet-stream", content_disposition=disposition)

    _CONTENT_TYPE_MAP = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".txt": "text/plain; charset=utf-8",
        ".mp4": "video/mp4",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".htm": "text/html; charset=utf-8",
        ".html": "text/html; charset=utf-8",
    }

    def _handle_view(self, parsed_url):
        file_path = self._resolve_requested_file(parsed_url)
        if file_path is None:
            return

        data = file_path.read_bytes()
        display_name = sanitize_filename(strip_timestamp_prefix(file_path.name))
        content_type = self._CONTENT_TYPE_MAP.get(file_path.suffix.lower(), "application/octet-stream")
        disposition = f'inline; filename="{display_name}"'
        self._send_bytes(data, content_type, content_disposition=disposition)

    def _handle_upload(self):
        student_name = self._name_from_query(urlparse(self.path).query)
        if not student_name:
            self._send_json({"error": "Unbekannter oder fehlender Name"}, status=HTTPStatus.FORBIDDEN)
            return

        content_type = self.headers.get("Content-Type", "")
        header = Message()
        header["content-type"] = content_type
        if header.get_content_type() != "multipart/form-data":
            self._send_json({"error": "Ungültige Anfrage"}, status=HTTPStatus.BAD_REQUEST)
            return

        boundary = header.get_param("boundary")
        try:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            self._send_json({"error": "Ungültige Anfrage"}, status=HTTPStatus.BAD_REQUEST)
            return

        if not boundary or content_length <= 0:
            self._send_json({"error": "Ungültige Anfrage"}, status=HTTPStatus.BAD_REQUEST)
            return

        if content_length > self.max_upload_size:
            self._send_json({"error": "Datei ist zu groß (max. 100 MB)"}, status=CONTENT_TOO_LARGE)
            return

        body = self.rfile.read(content_length)
        mime_blob = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
        message = BytesParser(policy=email_default_policy).parsebytes(mime_blob)

        uploaded_parts = []
        for part in message.iter_parts():
            field_name = part.get_param("name", header="content-disposition")
            if field_name != "files":
                continue
            uploaded_name = part.get_filename()
            uploaded_data = part.get_payload(decode=True) or b""
            if not uploaded_name:
                continue
            uploaded_parts.append((uploaded_name, uploaded_data))

        if not uploaded_parts:
            self._send_json({"error": "Keine Datei ausgewählt"}, status=HTTPStatus.BAD_REQUEST)
            return

        saved = []
        with self.state.lock:
            self.state.ensure_student_dirs(student_name)
            _, _, sent_dir = self.state.student_paths(student_name)
            for original_name, data in uploaded_parts:
                safe_original = sanitize_filename(original_name)
                prefixed = f"{timestamp_prefix()}__{safe_original}"
                target = safe_unique_path(sent_dir, prefixed)
                with open(target, "wb") as out:
                    out.write(data)
                saved.append({"filename": strip_timestamp_prefix(target.name), "size": len(data)})
            self.state.touch_active(student_name)
            self.state.push_file_list(student_name)

        if self.on_student_upload:
            for entry in saved:
                GLib.idle_add(self.on_student_upload, student_name, entry["filename"], entry["size"])
        self._notify_state_change()

        self._send_json({"ok": True, "saved": saved})

    def _handle_websocket(self):
        student_name = self._name_from_query(urlparse(self.path).query)
        if not student_name:
            self.send_error(HTTPStatus.FORBIDDEN, "Nicht erlaubt")
            return

        key = self.headers.get("Sec-WebSocket-Key", "")
        if "websocket" not in self.headers.get("Upgrade", "").lower() or not key:
            self.send_error(HTTPStatus.BAD_REQUEST, "Ungültiger WebSocket-Handshake")
            return

        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("utf-8")).digest()
        ).decode("ascii")

        self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()

        self.connection.settimeout(WS_TIMEOUT_SECONDS)
        with self.state.lock:
            self.state.add_socket(student_name, self.connection)
            _ws_send_json(self.connection, self.state.file_list_payload(student_name))
            self.state.touch_active(student_name)
        self._notify_state_change()

        try:
            while True:
                opcode, payload = _ws_recv_frame(self.connection)
                if opcode is None:
                    break
                if opcode == 0x8:
                    break
                if opcode == 0x9:
                    pong = bytes([0x8A, len(payload)]) + payload
                    self.connection.sendall(pong)
                if opcode == 0x1:
                    try:
                        data = json.loads(payload.decode("utf-8"))
                        if data.get("type") == "ping":
                            _ws_send_json(self.connection, {"type": "pong"})
                    except Exception:
                        pass
        except OSError:
            pass
        finally:
            self.connection.settimeout(None)
            with self.state.lock:
                self.state.remove_socket(student_name, self.connection)
            self._notify_state_change()


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
        except AttributeError:
            pass
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

        self.target_combo = Gtk.ComboBoxText()
        self.target_combo.append_text("Alle Schüler")
        self.target_combo.set_active(0)
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

    def _toggle_fullscreen(self, *_):
        self._is_fullscreen = not self._is_fullscreen
        if self._is_fullscreen:
            self.fullscreen()
            self._fullscreen_btn.set_icon_name("view-restore-symbolic")
        else:
            self.unfullscreen()
            self._fullscreen_btn.set_icon_name("view-fullscreen-symbolic")

    def _show_qr_fullscreen(self, *_):
        if qrcode is None:
            self.toast_overlay.add_toast(Adw.Toast(title="qrcode nicht installiert (pip install qrcode[pil])"))
            return

        win = Adw.Window()
        win.set_title("ClassShare QR-Code")
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
        if self.state.server_port:
            self._set_qr(qr_pic, self._url_for_students())
        box.append(qr_pic)

        url = self._url_for_students() if self.state.server_port else ""
        ip_lbl = Gtk.Label(label=url)
        ip_lbl.set_selectable(True)
        try:
            ip_lbl.add_css_class("title-2")
        except AttributeError:
            pass
        box.append(ip_lbl)

        hint = Gtk.Label(label="Klick oder Escape zum Schließen")
        try:
            hint.add_css_class("caption")
        except AttributeError:
            pass
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
                application_name="ClassShare",
                version="1.0",
                comments="Dateien teilen und einsammeln im Schulnetz",
                license_type=Gtk.License.GPL_3_0,
                developers=["GitHub Copilot (KI)"],
                website="https://github.com/TutorNachhilfe/gnome-classshare",
                issue_url="https://github.com/TutorNachhilfe/gnome-classshare/issues",
            )
            dialog.present(self)
            return
        except AttributeError:
            pass

    def _show_shortcuts(self, *_):
        try:
            xml = """<?xml version="1.0" encoding="UTF-8"?>
<interface>
  <object class="GtkShortcutsWindow" id="win">
    <property name="modal">1</property>
    <child>
      <object class="GtkShortcutsSection">
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title">Datei</property>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="title">Datei öffnen</property>
                <property name="accelerator">&lt;Primary&gt;o</property>
              </object>
            </child>
          </object>
        </child>
      </object>
    </child>
  </object>
</interface>"""
            builder = Gtk.Builder.new_from_string(xml, -1)
            win = builder.get_object("win")
            win.set_transient_for(self)
            win.present()
        except Exception:
            pass

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

    def _extract_paths_from_file_model(self, files_model):
        paths = []
        if files_model is None:
            return paths
        if hasattr(files_model, "get_n_items") and hasattr(files_model, "get_item"):
            for index in range(files_model.get_n_items()):
                file = files_model.get_item(index)
                if isinstance(file, Gio.File):
                    path = file.get_path()
                    if path:
                        paths.append(path)
            return paths
        return paths

    def _choose_file(self, *_):
        dialog = Gtk.FileChooserNative.new(
            "Datei auswählen",
            self,
            Gtk.FileChooserAction.OPEN,
            "Auswählen",
            "Abbrechen",
        )
        dialog.set_select_multiple(True)
        dialog.connect("response", self._on_file_response)
        dialog.show()

    def _on_file_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            paths = self._extract_paths_from_file_model(dialog.get_files())
            if paths:
                self._set_selected_files(paths)
                self.toast_overlay.add_toast(Adw.Toast(title=f"📂 {len(paths)} Datei(en) ausgewählt"))
        dialog.destroy()

    def _send_files_to_students(self, _btn):
        selected = list(self.state.selected_files)
        if not selected:
            self.toast_overlay.add_toast(Adw.Toast(title="Keine Datei ausgewählt"))
            return

        target_text = self.target_combo.get_active_text() or "Alle Schüler"
        all_students = self.state.student_names()
        if target_text == "Alle Schüler":
            target_students = all_students
        else:
            target_students = [target_text]

        if not target_students:
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
        picture.set_paintable(Gdk.Texture.new_for_pixbuf(pixbuf))

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
        previous = self.target_combo.get_active_text() or "Alle Schüler"
        names = self.state.student_names()
        self.target_combo.remove_all()
        self.target_combo.append_text("Alle Schüler")
        for name in names:
            self.target_combo.append_text(name)
        index = 0
        if previous != "Alle Schüler" and previous in names:
            index = names.index(previous) + 1
        self.target_combo.set_active(index)

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
        except Exception:
            self.toast_overlay.add_toast(Adw.Toast(title=f"Konnte Datei nicht öffnen: {Path(path).name}"))

    def _open_folder(self, _btn, folder: str):
        try:
            subprocess.Popen(["xdg-open", folder])
        except Exception:
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
                return
        except Exception:
            pass
        self.set_default_size(900, 740)

    def _save_settings(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps({"width": self.get_width(), "height": self.get_height()}))
        except Exception:
            pass

    def _on_close_request(self, *_):
        self._save_settings()
        return False


class ClassShareApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.tutornachhilfe.ClassShare")
        self.state = ClassShareState()
        self.server = None
        self.server_thread = None
        self.win = None

    def do_activate(self):
        try:
            # DEFAULT uses the system preference with libadwaita, including auto dark/light switching.
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)
        except Exception:
            pass

        self._ensure_desktop_file()
        self._install_icon()

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
                self.win.set_server_error(f"Port {SERVER_PORT} ist bereits belegt. Läuft das Programm schon?")
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
        ClassShareHandler.on_state_change = self._forward_state_change
        ClassShareHandler.on_student_upload = self._forward_student_upload

        self.server = ThreadingHTTPServer(("0.0.0.0", SERVER_PORT), ClassShareHandler)
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

    def _install_icon(self):
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
            except FileNotFoundError:
                pass
        except Exception:
            pass

    def _ensure_desktop_file(self):
        try:
            desktop_dir = Path.home() / ".local" / "share" / "applications"
            desktop_dir.mkdir(parents=True, exist_ok=True)
            desktop_path = desktop_dir / APP_DESKTOP_ID
            if not desktop_path.exists():
                exec_path = Path(sys.argv[0]).resolve()
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
        except Exception:
            pass


def main():
    app = ClassShareApp()
    app.run([])


if __name__ == "__main__":
    main()
