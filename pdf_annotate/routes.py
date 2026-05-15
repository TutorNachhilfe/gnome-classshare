import base64
import hashlib
import json
import logging
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from constants import CLASSSHARE_ROOT, WS_TIMEOUT_SECONDS
from state import _ws_recv_frame
from pdf_annotate.storage import load_annotations, save_annotations
from pdf_annotate.ws_relay import relay

# Bundled PDF.js UMD build – shipped with the repository so the project works
# offline on all devices immediately after `git clone`.
_STATIC_DIR = Path(__file__).parent / "static"
_PDFJS_MAIN   = _STATIC_DIR / "pdf.min.js"
_PDFJS_WORKER = _STATIC_DIR / "pdf.worker.min.js"


class AnnotationRoutes:
    @staticmethod
    def _pdfjs_main_url() -> str:
        """Return the URL the browser should use to load pdf.js."""
        return "/pdf-js/pdf.min.js"

    @staticmethod
    def _pdfjs_worker_url() -> str:
        """Return the URL the browser should use to load pdf.worker.js."""
        return "/pdf-js/pdf.worker.min.js"

    @staticmethod
    def _decode_pdf_id(pdf_id: str) -> Path | None:
        """Decode a base64url pdf_id and return the validated absolute PDF path, or None."""
        try:
            padded = pdf_id + "=" * (-len(pdf_id) % 4)
            decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
        except Exception:
            return None

        if "?" in decoded:
            parsed = urlparse(decoded)
            params = parse_qs(parsed.query)
            student_name = params.get("name", [""])[0]
            scope = params.get("scope", [""])[0]
            stored_name = params.get("file", [""])[0]
            if not student_name or not stored_name:
                return None
            if scope not in ("received", "sent"):
                return None
            for component in (student_name, stored_name):
                if not component or "\x00" in component or "/" in component or "\\" in component:
                    return None
            if Path(stored_name).name != stored_name:
                return None
            if scope == "received":
                pdf_path = CLASSSHARE_ROOT / student_name / "empfangen" / stored_name
            else:
                pdf_path = CLASSSHARE_ROOT / student_name / "gesendet" / stored_name
        else:
            pdf_path = Path(decoded)

        try:
            pdf_path.resolve().relative_to(CLASSSHARE_ROOT.resolve())
        except ValueError:
            return None

        return pdf_path

    @staticmethod
    def handle_pdf_viewer(handler, parsed_url):
        """Serve viewer.html."""
        viewer_path = Path(__file__).parent / "viewer.html"
        try:
            content = viewer_path.read_bytes()
            handler._send_bytes(content, "text/html; charset=utf-8")
        except OSError as exc:
            logging.error("viewer.html konnte nicht geladen werden: %s", exc)
            handler._send_html(
                "<h1>Fehler: viewer.html nicht gefunden</h1>",
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    @staticmethod
    def handle_pdfjs_main(handler, parsed_url):
        """Serve the bundled pdf.min.js."""
        handler._send_bytes(_PDFJS_MAIN.read_bytes(), "application/javascript; charset=utf-8")

    @staticmethod
    def handle_pdfjs_worker(handler, parsed_url):
        """Serve the bundled pdf.worker.min.js."""
        handler._send_bytes(_PDFJS_WORKER.read_bytes(), "application/javascript; charset=utf-8")

    @staticmethod
    def handle_pdf_file(handler, parsed_url):
        """Serve the raw PDF bytes identified by base64url pdf_id."""
        params = parse_qs(parsed_url.query)
        pdf_id = params.get("pdf", [""])[0]
        if not pdf_id:
            handler._send_html("<h1>Fehlender pdf-Parameter</h1>", status=HTTPStatus.BAD_REQUEST)
            return

        pdf_path = AnnotationRoutes._decode_pdf_id(pdf_id)
        if pdf_path is None:
            handler._send_html("<h1>Ungültige pdf_id</h1>", status=HTTPStatus.BAD_REQUEST)
            return

        if pdf_path.suffix.lower() != ".pdf":
            handler._send_html("<h1>Keine PDF-Datei</h1>", status=HTTPStatus.BAD_REQUEST)
            return

        if not pdf_path.is_file():
            handler._send_html("<h1>PDF nicht gefunden</h1>", status=HTTPStatus.NOT_FOUND)
            return

        try:
            handler._send_bytes(pdf_path.read_bytes(), "application/pdf")
        except OSError as exc:
            logging.error("PDF konnte nicht gelesen werden: %s", exc)
            handler._send_html(
                "<h1>Fehler beim Lesen der Datei</h1>",
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    @staticmethod
    def handle_annotations_get(handler, parsed_url):
        """GET /api/annotations?pdf=<pdf_id> – return stored annotations as JSON."""
        params = parse_qs(parsed_url.query)
        pdf_id = params.get("pdf", [""])[0]
        if not pdf_id:
            handler._send_json({"error": "Fehlender pdf-Parameter"}, status=HTTPStatus.BAD_REQUEST)
            return

        pdf_path = AnnotationRoutes._decode_pdf_id(pdf_id)
        if pdf_path is None:
            handler._send_json({"error": "Ungültige pdf_id"}, status=HTTPStatus.BAD_REQUEST)
            return

        handler._send_json(load_annotations(pdf_path))

    @staticmethod
    def handle_annotations_post(handler, parsed_url):
        """POST /api/annotations?pdf=<pdf_id> – replace all annotations."""
        params = parse_qs(parsed_url.query)
        pdf_id = params.get("pdf", [""])[0]
        if not pdf_id:
            handler._send_json({"error": "Fehlender pdf-Parameter"}, status=HTTPStatus.BAD_REQUEST)
            return

        pdf_path = AnnotationRoutes._decode_pdf_id(pdf_id)
        if pdf_path is None:
            handler._send_json({"error": "Ungültige pdf_id"}, status=HTTPStatus.BAD_REQUEST)
            return

        try:
            content_length = int(handler.headers.get("Content-Length", "0") or "0")
        except ValueError:
            handler._send_json({"error": "Ungültige Anfrage"}, status=HTTPStatus.BAD_REQUEST)
            return
        if content_length <= 0 or content_length > 10 * 1024 * 1024:
            handler._send_json({"error": "Ungültige Anfrage"}, status=HTTPStatus.BAD_REQUEST)
            return

        body = handler.rfile.read(content_length)
        try:
            data = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            handler._send_json({"error": "Ungültiges JSON"}, status=HTTPStatus.BAD_REQUEST)
            return

        if not isinstance(data, dict):
            handler._send_json({"error": "Ungültige Daten"}, status=HTTPStatus.BAD_REQUEST)
            return

        try:
            save_annotations(pdf_path, data)
        except OSError as exc:
            logging.error("Annotationen konnten nicht gespeichert werden: %s", exc)
            handler._send_json(
                {"error": "Fehler beim Speichern"}, status=HTTPStatus.INTERNAL_SERVER_ERROR
            )
            return

        handler._send_json({"ok": True})

    @staticmethod
    def handle_annotation_ws(handler, parsed_url):
        """WebSocket /ws/annotate?pdf=<pdf_id> – live annotation sync."""
        params = parse_qs(parsed_url.query)
        pdf_id = params.get("pdf", [""])[0]
        if not pdf_id:
            handler.send_error(HTTPStatus.BAD_REQUEST, "Fehlender pdf-Parameter")
            return

        key = handler.headers.get("Sec-WebSocket-Key", "")
        if "websocket" not in handler.headers.get("Upgrade", "").lower() or not key:
            handler.send_error(HTTPStatus.BAD_REQUEST, "Ungültiger WebSocket-Handshake")
            return

        accept = base64.b64encode(
            hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("utf-8")
            ).digest()
        ).decode("ascii")

        handler.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
        handler.send_header("Upgrade", "websocket")
        handler.send_header("Connection", "Upgrade")
        handler.send_header("Sec-WebSocket-Accept", accept)
        handler.end_headers()

        conn = handler.connection
        conn.settimeout(WS_TIMEOUT_SECONDS)
        relay.join(pdf_id, conn)

        try:
            while True:
                opcode, payload = _ws_recv_frame(conn)
                if opcode is None or opcode == 0x8:
                    break
                if opcode == 0x9:  # ping → pong
                    conn.sendall(bytes([0x8A, len(payload)]) + payload)
                if opcode == 0x1:  # text frame: relay to all other clients
                    relay.broadcast(pdf_id, payload, exclude=conn)
        except OSError as exc:
            logging.debug("Annotation WebSocket beendet: %s", exc)
        finally:
            conn.settimeout(None)
            relay.leave(pdf_id, conn)
