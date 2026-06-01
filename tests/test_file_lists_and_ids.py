"""Tests for file list payloads and shared annotation ids."""
import base64
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from state import ClassShareState


def _decode_pdf_id(pdf_id: str) -> str:
    padded = pdf_id + "=" * (-len(pdf_id) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8")


def test_file_list_payload_includes_received_and_sent_with_pdf_ids():
    with tempfile.TemporaryDirectory() as tmp:
        state = ClassShareState()
        state.base_dir = Path(tmp)
        _, received_dir, sent_dir = state.ensure_student_dirs("Anna")
        (received_dir / "20260101_120000_000001__blatt.pdf").write_bytes(b"r")
        (sent_dir / "20260101_120001_000002__antwort.pdf").write_bytes(b"s")

        payload = state.file_list_payload("Anna")

        assert len(payload["received"]) == 1
        assert len(payload["sent"]) == 1
        for item in payload["received"] + payload["sent"]:
            assert item["pdf_id"]
            assert _decode_pdf_id(item["pdf_id"]) == item["download"]


def test_tutor_overview_rows_include_both_file_lists_with_pdf_ids():
    with tempfile.TemporaryDirectory() as tmp:
        state = ClassShareState()
        state.base_dir = Path(tmp)
        _, received_dir, sent_dir = state.ensure_student_dirs("Anna")
        (received_dir / "20260101_120000_000001__blatt.pdf").write_bytes(b"r")
        (sent_dir / "20260101_120001_000002__antwort.pdf").write_bytes(b"s")

        rows = state.tutor_overview_rows()

        assert len(rows) == 1
        row = rows[0]
        assert len(row["received_files"]) == 1
        assert len(row["sent_files"]) == 1
        for item in row["received_files"] + row["sent_files"]:
            assert item["pdf_id"]
            assert _decode_pdf_id(item["pdf_id"]) == item["download"]


def test_student_template_mentions_sent_list_and_annotate_button_label():
    student_path = Path(__file__).resolve().parent.parent / "student.html"
    content = student_path.read_text(encoding="utf-8")

    assert "(data.sent || [])" in content
    assert "\\u{1F4DD} Annotieren" in content


def test_student_template_has_darkmode_variables_for_used_selectors():
    student_path = Path(__file__).resolve().parent.parent / "student.html"
    content = student_path.read_text(encoding="utf-8")

    assert "--bg: #f5f5f5;" in content
    assert "--card: #fff;" in content
    assert "--border: #ddd;" in content
    assert "@media (prefers-color-scheme: dark)" in content
    assert "--bg: #1a1a1a;" in content
    assert "--card: #2a2a2a;" in content
    assert "--border: #444;" in content


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
