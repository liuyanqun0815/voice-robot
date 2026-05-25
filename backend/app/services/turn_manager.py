from dataclasses import dataclass
from threading import Lock


@dataclass
class TurnState:
    committed: bool = False
    generation_id: str = ""


class TurnManager:
    def __init__(self) -> None:
        self._lock = Lock()
        self._states: dict[tuple[str, str], TurnState] = {}

    def commit_turn_once(self, session_id: str, turn_id: str) -> bool:
        key = (session_id, turn_id)
        with self._lock:
            state = self._states.setdefault(key, TurnState())
            if state.committed:
                return False
            state.committed = True
            state.generation_id = f"g_{turn_id}"
            return True

    def get_generation_id(self, session_id: str, turn_id: str) -> str:
        key = (session_id, turn_id)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                return ""
            return state.generation_id

    def cancel_generation(self, session_id: str, turn_id: str, generation_id: str) -> bool:
        key = (session_id, turn_id)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                return False
            if state.generation_id != generation_id:
                return False
            state.generation_id = ""
            return True

    def is_generation_active(self, session_id: str, turn_id: str, generation_id: str) -> bool:
        """generation 未被 cancel 时返回 True（generation_id 非空且与登记一致）。"""
        if not generation_id:
            return False
        key = (session_id, turn_id)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                return False
            return state.generation_id == generation_id
