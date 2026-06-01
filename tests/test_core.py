"""Pytest-Tests für die Kernlogik: sanitize_filename, sanitize_student_name, _decode_pdf_id, HTTP-Login."""
import base64
import io
import json
import os
import sys
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from utils import sanitize_filename, sanitize_student_name


# ---------------------------------------------------------------------------
# sanitize_filename
# ---------------------------------------------------------------------------

class TestSanitizeFilename:
    def test_valid_simple(self):
        assert sanitize_filename("test.pdf") == "test.pdf"

    def test_valid_with_spaces(self):
        assert sanitize_filename("mein dokument.pdf") == "mein dokument.pdf"

    def test_valid_umlauts(self):
        assert sanitize_filename("Übung.pdf") == "Übung.pdf"

    def test_path_traversal_dotdot(self):
        result = sanitize_filename("../secret.txt")
        assert ".." not in result
        assert "/" not in result

    def test_path_traversal_slash(self):
        result = sanitize_filename("/etc/passwd")
        assert "/" not in result

    def test_path_traversal_backslash(self):
        result = sanitize_filename("..\\windows\\system32\\cmd.exe")
        assert "\\" not in result

    def test_null_byte(self):
        result = sanitize_filename("file\x00name.txt")
        assert "\x00" not in result

    def test_control_chars(self):
        result = sanitize_filename("file\x01\x1fname.txt")
        assert "\x01" not in result
        assert "\x1f" not in result

    def test_empty_string_returns_default(self):
        assert sanitize_filename("") == "datei"

    def test_only_dots_returns_default(self):
        assert sanitize_filename("...") == "datei"

    def test_max_length(self):
        long_name = "a" * 300 + ".txt"
        result = sanitize_filename(long_name)
        assert len(result) <= 180

    def test_preserves_extension(self):
        result = sanitize_filename("homework.pdf")
        assert result.endswith(".pdf")

    def test_tab_replaced(self):
        result = sanitize_filename("file\tname.txt")
        assert "\t" not in result


# ---------------------------------------------------------------------------
# sanitize_student_name
# ---------------------------------------------------------------------------

class TestSanitizeStudentName:
    def test_valid_simple(self):
        assert sanitize_student_name("Max Mustermann") == "Max Mustermann"

    def test_valid_umlauts(self):
        result = sanitize_student_name("Müller")
        assert result  # must not be empty

    def test_valid_hyphen(self):
        result = sanitize_student_name("Anna-Lena")
        assert result == "Anna-Lena"

    def test_empty_string(self):
        assert sanitize_student_name("") == ""

    def test_whitespace_only(self):
        assert sanitize_student_name("   ") == ""

    def test_invalid_chars_stripped(self):
        result = sanitize_student_name("Max<script>alert(1)</script>")
        assert "<" not in result
        assert ">" not in result

    def test_null_byte_stripped(self):
        result = sanitize_student_name("Max\x00Muster")
        assert "\x00" not in result

    def test_path_traversal_stripped(self):
        result = sanitize_student_name("../admin")
        assert ".." not in result
        assert "/" not in result

    def test_max_length(self):
        long_name = "A" * 100
        result = sanitize_student_name(long_name)
        assert len(result) <= 64

    def test_multiple_spaces_collapsed(self):
        result = sanitize_student_name("Max   Mustermann")
        assert "  " not in result

    def test_leading_trailing_stripped(self):
        result = sanitize_student_name("  Max  ")
        assert result == "Max"

    def test_numbers_allowed(self):
        assert sanitize_student_name("Schüler1") == "Schüler1"


# ---------------------------------------------------------------------------
# _decode_pdf_id from pdf_annotate/routes.py
# ---------------------------------------------------------------------------

class TestDecodePdfId:
    def _encode(self, value: str) -> str:
        return base64.urlsafe_b64encode(value.encode("utf-8")).rstrip(b"=").decode("ascii")

    def test_valid_received_id(self, tmp_path):
        student = "Anna"
        filename = "homework.pdf"
        url = f"/download?name={student}&scope=received&file={filename}"
        pdf_id = self._encode(url)

        # Patch CLASSSHARE_ROOT to tmp_path so path validation passes
        with patch("pdf_annotate.routes.CLASSSHARE_ROOT", tmp_path):
            from pdf_annotate.routes import AnnotationRoutes
            result = AnnotationRoutes._decode_pdf_id(pdf_id)

        assert result is not None
        assert result == tmp_path / student / "empfangen" / filename

    def test_valid_sent_id(self, tmp_path):
        student = "Bob"
        filename = "notes.pdf"
        url = f"/download?name={student}&scope=sent&file={filename}"
        pdf_id = self._encode(url)

        with patch("pdf_annotate.routes.CLASSSHARE_ROOT", tmp_path):
            from pdf_annotate.routes import AnnotationRoutes
            result = AnnotationRoutes._decode_pdf_id(pdf_id)

        assert result is not None
        assert result == tmp_path / student / "gesendet" / filename

    def test_invalid_base64(self):
        from pdf_annotate.routes import AnnotationRoutes
        result = AnnotationRoutes._decode_pdf_id("!!!not-base64!!!")
        assert result is None

    def test_path_traversal_in_filename(self):
        url = "/download?name=Anna&scope=received&file=../../etc/passwd"
        pdf_id = self._encode(url)
        from pdf_annotate.routes import AnnotationRoutes
        result = AnnotationRoutes._decode_pdf_id(pdf_id)
        assert result is None

    def test_path_traversal_in_student_name(self):
        url = "/download?name=../admin&scope=received&file=homework.pdf"
        pdf_id = self._encode(url)
        from pdf_annotate.routes import AnnotationRoutes
        result = AnnotationRoutes._decode_pdf_id(pdf_id)
        assert result is None

    def test_invalid_scope(self):
        url = "/download?name=Anna&scope=evil&file=homework.pdf"
        pdf_id = self._encode(url)
        from pdf_annotate.routes import AnnotationRoutes
        result = AnnotationRoutes._decode_pdf_id(pdf_id)
        assert result is None

    def test_missing_student_name(self):
        url = "/download?scope=received&file=homework.pdf"
        pdf_id = self._encode(url)
        from pdf_annotate.routes import AnnotationRoutes
        result = AnnotationRoutes._decode_pdf_id(pdf_id)
        assert result is None

    def test_null_byte_in_filename(self):
        url = "/download?name=Anna&scope=received&file=home\x00work.pdf"
        pdf_id = self._encode(url)
        from pdf_annotate.routes import AnnotationRoutes
        result = AnnotationRoutes._decode_pdf_id(pdf_id)
        assert result is None

    def test_slash_in_filename(self):
        url = "/download?name=Anna&scope=received&file=sub/dir/homework.pdf"
        pdf_id = self._encode(url)
        from pdf_annotate.routes import AnnotationRoutes
        result = AnnotationRoutes._decode_pdf_id(pdf_id)
        assert result is None

    def test_outside_classshare_root(self, tmp_path):
        # A plain (non-query) path that resolves outside CLASSSHARE_ROOT
        pdf_id = self._encode("/etc/passwd")
        with patch("pdf_annotate.routes.CLASSSHARE_ROOT", tmp_path):
            from pdf_annotate.routes import AnnotationRoutes
            result = AnnotationRoutes._decode_pdf_id(pdf_id)
        assert result is None


# ---------------------------------------------------------------------------
# HTTP-Handler login logic (_handle_api_login)
# ---------------------------------------------------------------------------

class DummyState:
    """Minimal state mock for handler login tests."""
    app_name = "ClassShare"
    logo_path = None

    def __init__(self):
        import threading
        self.lock = threading.RLock()
        self._names: dict[str, str] = {}

    def resolve_name(self, name: str) -> Optional[str]:
        return self._names.get(name.casefold())

    def ensure_student_dirs(self, name: str):
        pass

    def touch_active(self, name: str):
        pass


class DummyHandler:
    """Minimal handler stub that captures response data."""

    def __init__(self, body: bytes, content_type: str = "application/json"):
        self.state = DummyState()
        self._body = body
        self.headers = {"Content-Length": str(len(body)), "Content-Type": content_type}
        self.rfile = io.BytesIO(body)
        self._response_status = None
        self._response_body = None

    def _send_json(self, payload: dict, status=None):
        from http import HTTPStatus
        self._response_status = status or HTTPStatus.OK
        self._response_body = payload

    def _notify_state_change(self):
        pass

    # Delegate login handler to the real implementation
    def handle_login(self):
        from handler import ClassShareHandler
        ClassShareHandler.state = self.state
        ClassShareHandler.on_state_change = None
        # Call the real _handle_api_login via an unbound call
        ClassShareHandler._handle_api_login(self)  # type: ignore[arg-type]


class TestHandleApiLogin:
    def _make_handler(self, payload: dict) -> DummyHandler:
        body = json.dumps(payload).encode("utf-8")
        return DummyHandler(body)

    def test_valid_name_returns_ok(self):
        handler = self._make_handler({"name": "Max Mustermann"})
        handler.handle_login()
        assert handler._response_body is not None
        assert handler._response_body.get("ok") is True
        assert handler._response_body.get("name") == "Max Mustermann"

    def test_invalid_name_returns_error(self):
        from http import HTTPStatus
        handler = self._make_handler({"name": "!!!"})
        handler.handle_login()
        assert handler._response_status == HTTPStatus.BAD_REQUEST
        assert "error" in handler._response_body

    def test_empty_name_returns_error(self):
        from http import HTTPStatus
        handler = self._make_handler({"name": ""})
        handler.handle_login()
        assert handler._response_status == HTTPStatus.BAD_REQUEST

    def test_missing_name_key_returns_error(self):
        from http import HTTPStatus
        handler = self._make_handler({})
        handler.handle_login()
        assert handler._response_status == HTTPStatus.BAD_REQUEST

    def test_invalid_json_returns_error(self):
        from http import HTTPStatus
        handler = DummyHandler(b"not json at all")
        handler.handle_login()
        assert handler._response_status == HTTPStatus.BAD_REQUEST

    def test_oversized_payload_returns_error(self):
        from http import HTTPStatus
        big_body = b"x" * (17 * 1024)
        handler = DummyHandler(big_body)
        handler.handle_login()
        assert handler._response_status == HTTPStatus.BAD_REQUEST

    def test_empty_payload_returns_error(self):
        from http import HTTPStatus
        handler = DummyHandler(b"")
        handler.handle_login()
        assert handler._response_status == HTTPStatus.BAD_REQUEST
