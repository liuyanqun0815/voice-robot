from dataclasses import dataclass
from threading import Lock


@dataclass
class SessionState:
    status: str = "listening"


class SessionManager:
    def __init__(self) -> None:
        self._lock = Lock()
        self._sessions: dict[str, SessionState] = {}

    def get_or_create(self, session_id: str) -> SessionState:
        with self._lock:
            return self._sessions.setdefault(session_id, SessionState())

    def set_status(self, session_id: str, status: str) -> None:
        with self._lock:
            session = self._sessions.setdefault(session_id, SessionState())
            session.status = status
