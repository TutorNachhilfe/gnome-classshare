import base64
import hashlib
import json
import logging
from email.message import Message
from email.parser import BytesParser
from email.policy import default as email_default_policy
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from constants import CONTENT_TOO_LARGE, MAX_UPLOAD_SIZE_BYTES, WS_TIMEOUT_SECONDS
from state import _ws_recv_frame, _ws_send_json
from utils import (
    safe_unique_path,
    sanitize_filename,
    sanitize_student_name,
    strip_timestamp_prefix,
    timestamp_prefix,
)

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
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Cache-Control", "no-store")
        if content_disposition:
            self.send_header("Content-Disposition", self._safe_header_value(content_disposition))
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _make_disposition(self, disposition_type: str, filename: str) -> str:
        """Build a Content-Disposition value with RFC 5987 filename* encoding."""
        encoded_name = quote(filename, safe="")
        return f'{disposition_type}; filename="{filename}"; filename*=UTF-8\'\'{encoded_name}'

    def _send_html(self, html: str, status=HTTPStatus.OK):
        self._send_bytes(html.encode("utf-8"), "text/html; charset=utf-8", status=status)

    def _send_json(self, payload: dict, status=HTTPStatus.OK):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(data, "application/json; charset=utf-8", status=status)

    def _notify_state_change(self):
        if self.on_state_change:
            self.on_state_change()

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
        if parsed.path == "/logo":
            self._handle_logo()
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

    def _handle_logo(self):
        logo_path = self.state.logo_path
        if not logo_path:
            self._send_html("<h1>Nicht gefunden</h1>", status=HTTPStatus.NOT_FOUND)
            return
        path = Path(logo_path)
        suffix = path.suffix.lower()
        allowed = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml"}
        if suffix not in allowed or not path.is_file():
            self._send_html("<h1>Nicht gefunden</h1>", status=HTTPStatus.NOT_FOUND)
            return
        if path.stat().st_size > 10 * 1024 * 1024:
            self._send_html("<h1>Datei zu groß</h1>", status=CONTENT_TOO_LARGE)
            return
        self._send_bytes(path.read_bytes(), allowed[suffix])

    def _render_app_page(self):
        app_name = escape(self.state.app_name or "ClassShare")
        if self.state.logo_path:
            brand_html = f'<img src="/logo" alt="{app_name}" class="logo" onerror="this.onerror=null;this.style.display=\'none\'"> {app_name}'
        else:
            brand_html = f'&#x1F4DA; {app_name}'
        return """
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__APP_NAME__</title>
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
      .logo { filter: invert(1); }
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
    .logo { height: 1.4em; width: auto; vertical-align: middle; margin-right: .3rem; border-radius: 3px; }
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
        <div class="brand">__BRAND_HTML__</div>
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
    let allFiles = [];
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
      allFiles.forEach(function(file) { fileList.appendChild(renderRow(file)); });
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
""".replace("__APP_NAME__", app_name).replace("__BRAND_HTML__", brand_html)

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
        disposition = self._make_disposition("attachment", download_name)
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
        disposition = self._make_disposition("inline", display_name)
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
                self.on_student_upload(student_name, entry["filename"], entry["size"])
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
                    except Exception as exc:
                        logging.debug("Ungültige WebSocket-Nachricht von %s ignoriert: %s", student_name, exc)
        except OSError as exc:
            # Normaler Betriebsfall: Verbindung wurde beendet/unterbrochen.
            logging.debug("WebSocket-Verbindung für %s beendet: %s", student_name, exc)
        finally:
            self.connection.settimeout(None)
            with self.state.lock:
                self.state.remove_socket(student_name, self.connection)
            self._notify_state_change()
