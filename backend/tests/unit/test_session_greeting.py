import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.core.settings import Settings
from app.services.orchestrator import GREETING_TURN_ID, Orchestrator


def test_send_session_greeting_streams_deltas() -> None:
    sink: list[dict] = []
    orchestrator = Orchestrator(send_event=sink.append)
    orchestrator._settings = Settings(
        greeting_enabled=True,
        greeting_text="你好！",
        greeting_tts_enabled=False,
        greeting_stream_chunk_chars=2,
        greeting_stream_interval_ms=0,
    )

    asyncio.run(orchestrator.send_session_greeting("session-1"))

    types = [event["type"] for event in sink]
    assert types == ["greeting_delta", "greeting_delta", "greeting_complete"]
    assert sink[0]["text"] == "你好"
    assert sink[1]["text"] == "！"
    assert sink[0]["turn_id"] == GREETING_TURN_ID


def test_send_session_greeting_skipped_when_disabled() -> None:
    sink: list[dict] = []
    orchestrator = Orchestrator(send_event=sink.append)
    orchestrator._settings = Settings(greeting_enabled=False, greeting_text="不应出现")

    asyncio.run(orchestrator.send_session_greeting())

    assert sink == []


def test_greeting_text_unescapes_newlines() -> None:
    settings = Settings(greeting_text="第一行\\n第二行")
    assert settings.greeting_text == "第一行\n第二行"
