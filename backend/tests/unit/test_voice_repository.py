import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.repositories.voice_repository import VoiceRepository


def test_create_session_record() -> None:
    repo = VoiceRepository()

    session = repo.create_session("s1", "u1")

    assert session.session_id == "s1"
