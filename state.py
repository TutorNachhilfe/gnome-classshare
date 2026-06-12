import json
import logging
import shutil
import socket
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from constants import CLASSSHARE_ROOT
from utils import encode_file_id, format_size, get_local_ip, parse_timestamp_prefix, sanitize_student_name, strip_timestamp_prefix

logger = logging.getLogger(__name__)

def _ws_send_frame(sock: socket.socket, opcode: int, payload: bytes) -> bool:
    frame = bytearray([0x80 | opcode])
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


def _ws_send_text(sock: socket.socket, text: str) -> bool:
    return _ws_send_frame(sock, 0x1, text.encode("utf-8"))


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

    def _list_dir_files(self, directory: Path, student_name: str, scope: str) -> list[dict]:
        if not directory.exists():
            return []
        entries = []
        for fp in directory.iterdir():
            if not fp.is_file():
                continue
            if fp.name.endswith(".annotations.json"):
                continue
            stat = fp.stat()
            sent_at = parse_timestamp_prefix(fp.name)
            sort_dt = sent_at if sent_at else datetime.fromtimestamp(stat.st_mtime)
            download = f"/download?name={quote(student_name)}&scope={scope}&file={quote(fp.name)}"
            entries.append({
                "filename": strip_timestamp_prefix(fp.name),
                "stored_name": fp.name,
                "path": str(fp),
                "folder": str(fp.parent),
                "size": stat.st_size,
                "size_human": format_size(stat.st_size),
                "mtime": sort_dt.timestamp(),
                "timestamp": sort_dt.strftime("%d.%m.%Y %H:%M"),
                "download": download,
                "view": f"/view?name={quote(student_name)}&scope={scope}&file={quote(fp.name)}",
                "file_id": encode_file_id(download),
            })
        entries.sort(key=lambda e: e["mtime"], reverse=True)
        return entries

    def file_list_payload(self, student_name: str) -> dict:
        _, received_dir, sent_dir = self.student_paths(student_name)
        return {
            "type": "file_list",
            "received": self._list_dir_files(received_dir, student_name, "received"),
            "sent": self._list_dir_files(sent_dir, student_name, "sent"),
        }

    def tutor_overview_rows(self) -> list[dict]:
        rows: list[dict] = []
        for name in self.student_names():
            _, received_dir, sent_dir = self.student_paths(name)

            received_files_list = self._list_dir_files(received_dir, name, "received")
            sent_files_list = self._list_dir_files(sent_dir, name, "sent")
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
        with self.lock:
            payload = self.file_list_payload(student_name)
            for sock in list(self.ws_connections.get(student_name, set())):
                if not _ws_send_json(sock, payload):
                    self.remove_socket(student_name, sock)

    def push_new_file(self, student_name: str, filename: str, size: int) -> None:
        with self.lock:
            payload = {
                "type": "new_file",
                "filename": filename,
                "size": size,
            }
            for sock in list(self.ws_connections.get(student_name, set())):
                if not _ws_send_json(sock, payload):
                    self.remove_socket(student_name, sock)
            self.push_file_list(student_name)

    def rename_student(self, old_name: str, new_name: str) -> Optional[str]:
        """Renames student directory and internal state. Returns error string or None on success."""
        new_name = sanitize_student_name(new_name)
        if not new_name:
            return "Ungültiger Name"
        with self.lock:
            if old_name == new_name:
                return None
            if (self.base_dir / new_name).exists():
                return "Name existiert bereits"
            old_dir = self.base_dir / old_name
            if not old_dir.is_dir():
                return "Schüler nicht gefunden"
            try:
                old_dir.rename(self.base_dir / new_name)
            except OSError as exc:
                return f"Konnte nicht umbenannt werden: {exc}"
            if old_name in self.ws_connections:
                self.ws_connections[new_name] = self.ws_connections.pop(old_name)
            if old_name in self.last_active:
                self.last_active[new_name] = self.last_active.pop(old_name)
            return None

    def delete_student(self, name: str) -> Optional[str]:
        """Deletes student directory and cleans up state. Returns error string or None on success."""
        with self.lock:
            student_dir = self.base_dir / name
            if not student_dir.is_dir():
                return "Schüler nicht gefunden"
            socks = self.ws_connections.pop(name, set())
            for sock in list(socks):
                try:
                    sock.close()
                except OSError:
                    pass
            try:
                shutil.rmtree(student_dir)
            except OSError as exc:
                self.ws_connections[name] = socks
                return f"Konnte nicht gelöscht werden: {exc}"
            self.last_active.pop(name, None)
            return None
