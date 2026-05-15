"""Focused tests for locally served PDF.js assets."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pdf_annotate import routes


class DummyHandler:
    def __init__(self):
        self.sent_bytes = None
        self.responses = []
        self.headers = []
        self.ended = False

    def _send_bytes(self, content, content_type):
        self.sent_bytes = (content, content_type)

    def send_response(self, status):
        self.responses.append(status)

    def send_header(self, name, value):
        self.headers.append((name, value))

    def end_headers(self):
        self.ended = True


class FakePath:
    def __init__(self, content: bytes | None):
        self._content = content

    def is_file(self):
        return self._content is not None

    def read_bytes(self):
        return self._content


def test_handle_pdfjs_main_serves_local_asset():
    handler = DummyHandler()
    local_paths = (FakePath(b"console.log('local');"), FakePath(None))
    with patch.object(routes, "PDFJS_MAIN_PATHS", local_paths):
        routes.AnnotationRoutes.handle_pdfjs_main(handler, None)

    assert handler.sent_bytes == (
        b"console.log('local');",
        "application/javascript; charset=utf-8",
    )
    assert handler.responses == []


def test_handle_pdfjs_worker_redirects_to_cdn_without_local_asset():
    handler = DummyHandler()
    local_paths = (FakePath(None), FakePath(None))
    with patch.object(routes, "PDFJS_WORKER_PATHS", local_paths):
        routes.AnnotationRoutes.handle_pdfjs_worker(handler, None)

    assert handler.sent_bytes is None
    assert handler.responses == [302]
    assert (
        "Location",
        "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.0.379/pdf.worker.min.js",
    ) in handler.headers
    assert handler.ended is True


def test_viewer_uses_local_pdfjs_assets_and_error_message():
    viewer_path = Path(__file__).resolve().parent.parent / "pdf_annotate" / "viewer.html"
    content = viewer_path.read_text(encoding="utf-8")

    assert '<script src="/pdf-js/pdf.min.js"></script>' in content
    assert "pdfjsLib.GlobalWorkerOptions.workerSrc =" in content
    assert "'/pdf-js/pdf.worker.min.js';" in content
    assert "PDF.js nicht verfügbar" in content


if __name__ == "__main__":
    import traceback
    import sys

    tests = [v for k, v in globals().items() if k.startswith("test_")]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  ✓ {test.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  ✗ {test.__name__}: {exc}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
