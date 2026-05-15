import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.services.turn_manager import TurnManager


def test_commit_turn_once_only_first_commit_succeeds() -> None:
    manager = TurnManager()

    first_commit = manager.commit_turn_once(session_id="session-1", turn_id="turn-1")
    second_commit = manager.commit_turn_once(session_id="session-1", turn_id="turn-1")

    assert first_commit is True
    assert second_commit is False
    assert manager.get_generation_id(session_id="session-1", turn_id="turn-1") == "g_turn-1"


def test_cancel_generation_returns_false_on_generation_id_mismatch() -> None:
    manager = TurnManager()
    manager.commit_turn_once(session_id="session-1", turn_id="turn-2")

    cancelled = manager.cancel_generation(
        session_id="session-1",
        turn_id="turn-2",
        generation_id="g_other-turn",
    )

    assert cancelled is False
