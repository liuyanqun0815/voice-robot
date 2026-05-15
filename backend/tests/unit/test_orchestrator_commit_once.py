import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.services.orchestrator import Orchestrator


def test_asr_partial_is_forwarded_to_client() -> None:
    sink: list[dict[str, str]] = []
    orchestrator = Orchestrator(send_event=sink.append)

    orchestrator.on_asr_partial("s1", "t1", "你好")

    assert sink[0]["type"] == "asr_partial"
    assert sink[0]["text"] == "你好"


def test_run_turn_emits_llm_tts_and_complete() -> None:
    sink: list[dict[str, str]] = []
    orchestrator = Orchestrator(send_event=sink.append)

    asyncio.run(orchestrator.run_turn("s1", "t1", "帮我查订单"))

    event_types = [event["type"] for event in sink]
    assert "llm_delta" in event_types
    assert "tts_chunk" in event_types
    assert event_types[-1] == "audio_complete"
