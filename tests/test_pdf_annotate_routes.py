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

    assert "touch-action: none;" in content
    assert "if (e.pointerType === 'touch') return;" in content
    assert "annCanvas.addEventListener('pointerdown', e => {" in content
    assert "annCanvas.addEventListener('pointermove', e => {" in content
    assert "annCanvas.addEventListener('pointerup', e => {" in content


def test_viewer_has_pointercancel_handler():
    viewer_path = Path(__file__).resolve().parent.parent / "pdf_annotate" / "viewer.html"
    content = viewer_path.read_text(encoding="utf-8")

    assert "annCanvas.addEventListener('pointercancel', e => {" in content
    assert "annCanvas.releasePointerCapture(e.pointerId);" in content


def test_viewer_setTool_updates_touch_action():
    viewer_path = Path(__file__).resolve().parent.parent / "pdf_annotate" / "viewer.html"
    content = viewer_path.read_text(encoding="utf-8")

    assert "p.annCanvas.style.touchAction = active ? 'none' : 'pan-x pan-y pinch-zoom';" in content


def test_viewer_pointermove_uses_coalesced_events():
    viewer_path = Path(__file__).resolve().parent.parent / "pdf_annotate" / "viewer.html"
    content = viewer_path.read_text(encoding="utf-8")

    assert "e.getCoalescedEvents ? e.getCoalescedEvents() : [e]" in content
    assert "for (const ev of events) {" in content


def test_viewer_btnSave_posts_to_bake_endpoint():
    viewer_path = Path(__file__).resolve().parent.parent / "pdf_annotate" / "viewer.html"
    content = viewer_path.read_text(encoding="utf-8")

    assert "/api/annotations/bake?pdf=" in content
    assert "method: 'POST'," in content
    assert "'Content-Type': 'application/pdf'" in content
    assert "Server-Upload fehlgeschlagen" in content


def test_viewer_pen_button_cycles_and_starts_enabled():
    viewer_path = Path(__file__).resolve().parent.parent / "pdf_annotate" / "viewer.html"
    content = viewer_path.read_text(encoding="utf-8")

    assert "if (tool === 'none') setTool('pen');" in content
    assert "else if (tool === 'pen') setTool('eraser');" in content
    assert "else setTool('none');" in content
    assert "btnPen.textContent = '✏️ Stift ✓';" in content
    assert "btnPen.textContent = '🧹 Radierer';" in content
    assert "setTool('pen');" in content


def test_viewer_has_pdf_save_button_and_pdf_lib_export_logic():
    viewer_path = Path(__file__).resolve().parent.parent / "pdf_annotate" / "viewer.html"
    content = viewer_path.read_text(encoding="utf-8")

    assert '<button id="btnSave" title="Als PDF speichern">💾 Speichern</button>' in content
    assert '<script src="https://unpkg.com/pdf-lib/dist/pdf-lib.min.js"></script>' in content
    assert "const pdfUrl = `/pdf-file?pdf=${encodeURIComponent(pdfId)}`;" in content
    assert "btnSave.addEventListener('click', async () => {" in content
    assert "setStatus('❌ PDF-Export nicht verfügbar');" in content
    assert "const canvas = pages[i].annCanvas;" in content
    assert "const pngDataUrl = canvas.toDataURL('image/png');" in content
    assert "const pdfDoc = await PDFLib.PDFDocument.load(pdfBytes);" in content
    assert "page.drawImage(pngImage, { x: 0, y: 0, width, height });" in content
    assert "a.download = 'annotiert.pdf';" in content
    assert "console.error('PDF export failed:', err);" in content


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


def test_handle_annotations_bake_missing_pdf_param():
    import io
    from http import HTTPStatus
    from urllib.parse import urlparse

    class JsonHandler:
        def __init__(self):
            self.last_json = None
            self.last_status = None
            self.headers = {}
            self.rfile = io.BytesIO(b"")

        def _send_json(self, data, status=HTTPStatus.OK):
            self.last_json = data
            self.last_status = status

    handler = JsonHandler()
    parsed_url = urlparse("/api/annotations/bake")
    routes.AnnotationRoutes.handle_annotations_bake(handler, parsed_url)
    assert handler.last_status == HTTPStatus.BAD_REQUEST
    assert "error" in handler.last_json


def test_handle_annotations_bake_invalid_pdf_id():
    import io
    from http import HTTPStatus
    from urllib.parse import urlparse

    class JsonHandler:
        def __init__(self):
            self.last_json = None
            self.last_status = None
            self.headers = {}
            self.rfile = io.BytesIO(b"")

        def _send_json(self, data, status=HTTPStatus.OK):
            self.last_json = data
            self.last_status = status

    handler = JsonHandler()
    parsed_url = urlparse("/api/annotations/bake?pdf=!!!invalid!!!")
    routes.AnnotationRoutes.handle_annotations_bake(handler, parsed_url)
    assert handler.last_status == HTTPStatus.BAD_REQUEST


def test_handle_annotations_bake_success():
    import io
    import base64
    from http import HTTPStatus
    from urllib.parse import urlparse, quote

    from constants import CLASSSHARE_ROOT

    # Create a real PDF file inside CLASSSHARE_ROOT for the test
    pdf_dir = CLASSSHARE_ROOT / "_test_bake_student" / "empfangen"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf_file = pdf_dir / "test_bake.pdf"
    original_bytes = b"%PDF-1.4 original content"
    pdf_file.write_bytes(original_bytes)

    try:
        pdf_id = base64.urlsafe_b64encode(
            b"?name=_test_bake_student&scope=received&file=test_bake.pdf"
        ).rstrip(b"=").decode()

        new_bytes = b"%PDF-1.4 annotated content"

        class JsonHandler:
            def __init__(self):
                self.last_json = None
                self.last_status = HTTPStatus.OK
                self.rfile = io.BytesIO(new_bytes)
                self.headers = {"Content-Length": str(len(new_bytes))}

            def _send_json(self, data, status=HTTPStatus.OK):
                self.last_json = data
                self.last_status = status

        handler = JsonHandler()
        parsed_url = urlparse(f"/api/annotations/bake?pdf={quote(pdf_id)}")
        routes.AnnotationRoutes.handle_annotations_bake(handler, parsed_url)

        assert handler.last_json == {"ok": True}
        assert pdf_file.read_bytes() == new_bytes
        bak_file = pdf_file.with_suffix(".pdf.bak")
        assert bak_file.exists()
        assert bak_file.read_bytes() == original_bytes
    finally:
        import shutil
        shutil.rmtree(CLASSSHARE_ROOT / "_test_bake_student", ignore_errors=True)


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
