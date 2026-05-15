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
    _student_html_template: str | None = None

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
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _send_html(self, html: str, status=HTTPStatus.OK):
        self._send_bytes(html.encode("utf-8"), "text/html; charset=utf-8", status=status)

    def _send_json(self, payload: dict, status=HTTPStatus.OK):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(data, "application/json; charset=utf-8", status=status)

    def _make_disposition(self, disposition_type: str, filename: str) -> str:
        """Build a Content-Disposition header value with RFC 5987 encoding."""
        ascii_fallback = "".join(ch if ord(ch) < 128 else "_" for ch in filename)
        ascii_fallback = ascii_fallback.replace("\\", "\\\\").replace('"', '\\"')
        encoded = quote(filename, safe="")
        return f'{disposition_type}; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'

    def _notify_state_change(self):
        cb = ClassShareHandler.on_state_change
        if cb:
            cb()

    def _notify_student_upload(self, student_name: str, filename: str, size: int):
        cb = ClassShareHandler.on_student_upload
        if cb:
            cb(student_name, filename, size)

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
        if parsed.path == "/annotate":
            from pdf_annotate.routes import AnnotationRoutes
            AnnotationRoutes.handle_pdf_viewer(self, parsed)
            return
        if parsed.path == "/pdf-js/pdf.min.js":
            from pdf_annotate.routes import AnnotationRoutes
            AnnotationRoutes.handle_pdfjs_main(self, parsed)
            return
        if parsed.path == "/pdf-js/pdf.worker.min.js":
            from pdf_annotate.routes import AnnotationRoutes
            AnnotationRoutes.handle_pdfjs_worker(self, parsed)
            return
        if parsed.path == "/pdf-file":
            from pdf_annotate.routes import AnnotationRoutes
            AnnotationRoutes.handle_pdf_file(self, parsed)
            return
        if parsed.path == "/api/annotations":
            from pdf_annotate.routes import AnnotationRoutes
            AnnotationRoutes.handle_annotations_get(self, parsed)
            return
        if parsed.path == "/ws/annotate":
            from pdf_annotate.routes import AnnotationRoutes
            AnnotationRoutes.handle_annotation_ws(self, parsed)
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
        if path == "/api/annotations":
            from pdf_annotate.routes import AnnotationRoutes
            parsed_url = urlparse(self.path)
            AnnotationRoutes.handle_annotations_post(self, parsed_url)
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
        if ClassShareHandler._student_html_template is None:
            try:
                ClassShareHandler._student_html_template = (Path(__file__).parent / "student.html").read_text(encoding="utf-8")
            except OSError as exc:
                logging.error("student.html konnte nicht geladen werden: %s", exc)
                return "<h1>Fehler: student.html nicht gefunden</h1>"
        return ClassShareHandler._student_html_template.replace("__APP_NAME__", app_name).replace("__BRAND_HTML__", brand_html)

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
        self._send_bytes(data, "application/octet-stream", content_disposition=self._make_disposition("attachment", download_name))

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
        self._send_bytes(data, content_type, content_disposition=self._make_disposition("inline", display_name))

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

        for entry in saved:
            self._notify_student_upload(student_name, entry["filename"], entry["size"])
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
