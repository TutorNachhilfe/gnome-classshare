"""Grundlegende Tests für utils.py und constants.py."""
import base64
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from utils import encode_pdf_id, sanitize_filename, sanitize_student_name, strip_timestamp_prefix, timestamp_prefix


def test_sanitize_filename_basic():
    assert sanitize_filename("test.pdf") == "test.pdf"


def test_sanitize_filename_path_traversal():
    result = sanitize_filename("../secret.txt")
    assert ".." not in result
    assert "/" not in result


def test_sanitize_filename_null_byte():
    result = sanitize_filename("file\x00name.txt")
    assert "\x00" not in result


def test_sanitize_student_name_valid():
    assert sanitize_student_name("Max Mustermann") == "Max Mustermann"


def test_sanitize_student_name_empty():
    assert sanitize_student_name("") == ""


def test_sanitize_student_name_special_chars():
    # Umlaute erlaubt
    result = sanitize_student_name("Müller")
    assert result  # nicht leer


def test_strip_timestamp_prefix():
    assert strip_timestamp_prefix("20240101_120000__datei.pdf") == "datei.pdf"
    assert strip_timestamp_prefix("datei.pdf") == "datei.pdf"


def test_timestamp_prefix_format():
    prefix = timestamp_prefix()
    assert isinstance(prefix, str)
    assert len(prefix) > 0


def test_encode_pdf_id_roundtrip():
    raw = "/download?name=Anna&scope=received&file=test.pdf"
    encoded = encode_pdf_id(raw)
    assert isinstance(encoded, str)
    assert encoded
    padded = encoded + "=" * (-len(encoded) % 4)
    assert base64.urlsafe_b64decode(padded).decode("utf-8") == raw


def test_constants_types():
    from constants import MAX_UPLOAD_SIZE_BYTES, SERVER_PORT, WS_TIMEOUT_SECONDS
    assert isinstance(MAX_UPLOAD_SIZE_BYTES, int)
    assert MAX_UPLOAD_SIZE_BYTES > 0
    assert isinstance(SERVER_PORT, int)
    assert 1 <= SERVER_PORT <= 65535
    assert isinstance(WS_TIMEOUT_SECONDS, int)


if __name__ == "__main__":
    # Einfacher Test-Runner ohne pytest
    import traceback
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  ✓ {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {test.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
