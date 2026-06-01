"""Focused tests for locally served PDF.js assets."""
import os
import sys
from pathlib import Path

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


def test_handle_pdfjs_main_serves_bundled_asset():
    handler = DummyHandler()
    routes.AnnotationRoutes.handle_pdfjs_main(handler, None)

    assert handler.sent_bytes is not None
    content, content_type = handler.sent_bytes
    assert len(content) > 0
    assert content_type == "application/javascript; charset=utf-8"
    assert handler.responses == []


def test_handle_pdfjs_worker_serves_bundled_asset():
    handler = DummyHandler()
    routes.AnnotationRoutes.handle_pdfjs_worker(handler, None)

    assert handler.sent_bytes is not None
    content, content_type = handler.sent_bytes
    assert len(content) > 0
    assert content_type == "application/javascript; charset=utf-8"
    assert handler.responses == []


def test_viewer_uses_local_pdfjs_assets_and_error_message():
    viewer_path = Path(__file__).resolve().parent.parent / "pdf_annotate" / "viewer.html"
    content = viewer_path.read_text(encoding="utf-8")

    assert '<script src="/pdf-js/pdf.min.js"></script>' in content
    assert "typeof pdfjsLib === 'undefined'" in content
    assert "pdfjsLib.GlobalWorkerOptions.workerSrc =" in content
    assert "'/pdf-js/pdf.worker.min.js';" in content


def test_viewer_allows_touch_scroll_but_blocks_touch_drawing():
    viewer_path = Path(__file__).resolve().parent.parent / "pdf_annotate" / "viewer.html"
    content = viewer_path.read_text(encoding="utf-8")

    assert "touch-action: pan-x pan-y pinch-zoom;" in content
    assert "if (e.pointerType === 'touch') return;" in content
    assert "annCanvas.addEventListener('pointerdown', e => {" in content
    assert "annCanvas.addEventListener('pointermove', e => {" in content
    assert "annCanvas.addEventListener('pointerup', e => {" in content


def test_viewer_pen_button_cycles_and_starts_enabled():
    viewer_path = Path(__file__).resolve().parent.parent / "pdf_annotate" / "viewer.html"
    content = viewer_path.read_text(encoding="utf-8")

    assert "if (tool === 'none') setTool('pen');" in content
    assert "else if (tool === 'pen') setTool('eraser');" in content
    assert "else setTool('none');" in content
    assert "btnPen.textContent = '✏️ Stift ✓';" in content
    assert "btnPen.textContent = '🧹 Radierer';" in content
    assert "setTool('pen');" in content


def test_viewer_uses_light_theme_with_darkmode_override():
    viewer_path = Path(__file__).resolve().parent.parent / "pdf_annotate" / "viewer.html"
    content = viewer_path.read_text(encoding="utf-8")

    assert "body { background: #e0e0e0;" in content
    assert "#toolbar {" in content
    assert "background: #f5f5f5;" in content
    assert "color: #1a1a1a;" in content
    assert "@media (prefers-color-scheme: dark)" in content
    assert "body { background: #666; }" in content
    assert "background: #1e1e1e;" in content


def test_pdfjs_url_methods_return_fixed_paths():
    assert routes.AnnotationRoutes._pdfjs_main_url() == "/pdf-js/pdf.min.js"
    assert routes.AnnotationRoutes._pdfjs_worker_url() == "/pdf-js/pdf.worker.min.js"


if __name__ == "__main__":
    import traceback

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
