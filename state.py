import json
import logging
import socket
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from constants import CLASSSHARE_ROOT
from utils import encode_pdf_id, format_size, get_local_ip, parse_timestamp_prefix, strip_timestamp_prefix

logger = logging.getLogger(__name__)

def _ws_send_text(sock: socket.socket, text: str) -> bool:
    payload = text.encode("utf-8")
    frame = bytearray([0x81])
    size = len(payload)
    if size < 126:
        frame.append(size)
    elif size < 65536:
        frame.append(126)
        frame.extend(size.to_bytes(2, "big"))
    else:
        frame.append(127)
        frame.extend(size.to_bytes(8, "big"))
    frame.extend(payload)
    try:
        sock.sendall(frame)
        return True
    except OSError:
        return False


def _ws_send_json(sock: socket.socket, payload: dict) -> bool:
    return _ws_send_text(sock, json.dumps(payload, ensure_ascii=False))


def _ws_recv_frame(sock: socket.socket) -> tuple[Optional[int], bytes]:
    head = sock.recv(2)
    if len(head) < 2:
        return None, b""
    first, second = head
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        ext = sock.recv(2)
        if len(ext) < 2:
            return None, b""
        length = int.from_bytes(ext, "big")
    elif length == 127:
        ext = sock.recv(8)
        if len(ext) < 8:
            return None, b""
        length = int.from_bytes(ext, "big")

    mask = b""
    if masked:
        mask = sock.recv(4)
        if len(mask) < 4:
            return None, b""

    payload = b""
    while len(payload) < length:
        chunk = sock.recv(length - len(payload))
        if not chunk:
            return None, b""
        payload += chunk

    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

    return opcode, payload

class ClassShareState:
    def __init__(self) -> None:
        self.server_port: Optional[int] = None
        self.server_ip: str = get_local_ip()
        self.base_dir: Path = CLASSSHARE_ROOT
        self.base_dir.mkdir(parents=True, exist_ok=True)

        self.ws_connections: dict[str, set[socket.socket]] = {}
        self.last_active: dict[str, str] = {}
        self.selected_files: list[str] = []
        self.lock: threading.RLock = threading.RLock()
        self.app_name: str = "ClassShare"
        self.logo_path: Optional[str] = None

    def student_names(self) -> list[str]:
        names = [p.name for p in self.base_dir.iterdir() if p.is_dir()]
        names.sort(key=lambda name: name.casefold())
        return names

    def resolve_name(self, name: str) -> Optional[str]:
        lookup = {existing.casefold(): existing for existing in self.student_names()}
        return lookup.get(name.casefold())

    def ensure_student_dirs(self, name: str) -> tuple[Path, Path, Path]:
        student_dir = self.base_dir / name
        received_dir = student_dir / "empfangen"
        sent_dir = student_dir / "gesendet"
        received_dir.mkdir(parents=True, exist_ok=True)
        sent_dir.mkdir(parents=True, exist_ok=True)
        return student_dir, received_dir, sent_dir

    def student_paths(self, name: str) -> tuple[Path, Path, Path]:
        student_dir = self.base_dir / name
        return student_dir, student_dir / "empfangen", student_dir / "gesendet"

    def touch_active(self, name: str) -> None:
        self.last_active[name] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def file_list_payload(self, student_name: str) -> dict:
        _, received_dir, sent_dir = self.student_paths(student_name)

        def read_dir(path: Path, scope: str):
            items = []
            if not path.exists():
                return items
            files = [f for f in path.iterdir() if f.is_file()]
            for file_path in files:
                stat = file_path.stat()
                sent_at = parse_timestamp_prefix(file_path.name)
                sort_dt = sent_at if sent_at else datetime.fromtimestamp(stat.st_mtime)
                items.append(
                    {
                        "filename": strip_timestamp_prefix(file_path.name),
                        "stored_name": file_path.name,
                        "size": stat.st_size,
                        "size_human": format_size(stat.st_size),
                        "mtime": sort_dt.timestamp(),
                        "timestamp": sort_dt.strftime("%d.%m.%Y %H:%M"),
                        "download": f"/download?name={quote(student_name)}&scope={scope}&file={quote(file_path.name)}",
                        "view": f"/view?name={quote(student_name)}&scope={scope}&file={quote(file_path.name)}",
                    }
                )
            items.sort(key=lambda item: item["mtime"], reverse=True)
            for item in items:
                item["pdf_id"] = encode_pdf_id(item["download"])
            return items

        return {
            "type": "file_list",
            "received": read_dir(received_dir, "received"),
            "sent": read_dir(sent_dir, "sent"),
        }

    def tutor_overview_rows(self) -> list[dict]:
        rows: list[dict] = []
        names = self.student_names()
        for name in names:
            _, received_dir, sent_dir = self.student_paths(name)

            def build_file_list(path: Path, scope: str) -> list[dict]:
                files_list: list[dict] = []
                if not path.exists():
                    return files_list
                sorted_files = sorted([p for p in path.glob("*") if p.is_file()], key=lambda f: f.stat().st_mtime, reverse=True)
                for fp in sorted_files:
                    download = f"/download?name={quote(name)}&scope={scope}&file={quote(fp.name)}"
                    files_list.append(
                        {
                            "filename": strip_timestamp_prefix(fp.name),
                            "path": str(fp),
                            "folder": str(fp.parent),
                            "download": download,
                            "pdf_id": encode_pdf_id(download),
                        }
                    )
                return files_list

            received_files_list = build_file_list(received_dir, "received")
            sent_files_list = build_file_list(sent_dir, "sent")
            received_count = len(received_files_list)
            sent_count = len(sent_files_list)
            is_online = bool(self.ws_connections.get(name))
            rows.append(
                {
                    "name": name,
                    "received": received_count,
                    "sent": sent_count,
                    "received_files": received_files_list,
                    "sent_files": sent_files_list,
                    "last_active": self.last_active.get(name, "-"),
                    "online": is_online,
                }
            )
        return rows

    def sockets_for_name(self, student_name: str) -> list:
        sockets = self.ws_connections.get(student_name, set())
        return list(sockets)

    def add_socket(self, student_name: str, sock: socket.socket) -> None:
        if student_name not in self.ws_connections:
            self.ws_connections[student_name] = set()
        self.ws_connections[student_name].add(sock)
        logger.debug("WebSocket-Verbindung hinzugefügt für %s", student_name)

    def remove_socket(self, student_name: str, sock: socket.socket) -> None:
        sockets = self.ws_connections.get(student_name)
        if not sockets:
            return
        sockets.discard(sock)
        if not sockets:
            self.ws_connections.pop(student_name, None)
        logger.debug("WebSocket-Verbindung entfernt für %s", student_name)

    def push_file_list(self, student_name: str) -> None:
        payload = self.file_list_payload(student_name)
        for sock in list(self.sockets_for_name(student_name)):
            if not _ws_send_json(sock, payload):
                self.remove_socket(student_name, sock)

    def push_new_file(self, student_name: str, filename: str, size: int) -> None:
        payload = {
            "type": "new_file",
            "filename": filename,
            "size": size,
        }
        for sock in list(self.sockets_for_name(student_name)):
            if not _ws_send_json(sock, payload):
                self.remove_socket(student_name, sock)
        self.push_file_list(student_name)
