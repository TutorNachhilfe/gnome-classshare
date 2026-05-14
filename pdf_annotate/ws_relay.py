import threading


class AnnotationRelay:
    """Relay for broadcasting annotation WebSocket messages across clients sharing a PDF."""

    def __init__(self):
        self.sessions: dict[str, set] = {}
        self.lock = threading.Lock()

    def join(self, pdf_id: str, conn):
        """Register a WebSocket connection for a PDF session."""
        with self.lock:
            if pdf_id not in self.sessions:
                self.sessions[pdf_id] = set()
            self.sessions[pdf_id].add(conn)

    def leave(self, pdf_id: str, conn):
        """Remove a WebSocket connection from a PDF session."""
        with self.lock:
            if pdf_id in self.sessions:
                self.sessions[pdf_id].discard(conn)
                if not self.sessions[pdf_id]:
                    del self.sessions[pdf_id]

    def broadcast(self, pdf_id: str, message: bytes, exclude=None):
        """Send a raw text message to all connections in the room except the sender."""
        from state import _ws_send_text

        try:
            text = message.decode("utf-8")
        except Exception:
            return

        with self.lock:
            targets = set(self.sessions.get(pdf_id, set()))

        for conn in targets:
            if conn is exclude:
                continue
            try:
                _ws_send_text(conn, text)
            except Exception:
                pass


relay = AnnotationRelay()
