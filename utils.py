import base64
import re
import socket
from datetime import datetime
from pathlib import Path

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


def encode_pdf_id(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).rstrip(b"=").decode("ascii")


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


def parse_timestamp_prefix(filename: str) -> datetime | None:
    if "__" not in filename:
        return None
    prefix = filename.split("__", 1)[0]
    try:
        return datetime.strptime(prefix, "%Y%m%d_%H%M%S_%f")
    except ValueError:
        return None
