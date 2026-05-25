import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.schemas.events import AudioChunkEvent, CancelEvent, TurnCommitRequestEvent, VadEvent


def test_turn_commit_request_requires_reason() -> None:
    with pytest.raises(ValidationError):
        TurnCommitRequestEvent(
            type="turn_commit_request",
            session_id="session-1",
            turn_id="turn-1",
            seq=1,
            trace_id="trace-1",
            timestamp_ms=1715670000000,
        )


def test_turn_commit_request_valid_payload() -> None:
    event = TurnCommitRequestEvent(
        type="turn_commit_request",
        session_id="session-1",
        turn_id="turn-1",
        seq=1,
        trace_id="trace-1",
        timestamp_ms=1715670000000,
        reason="vad_timeout",
    )

    assert event.reason == "vad_timeout"


def test_turn_commit_request_text_mode() -> None:
    event = TurnCommitRequestEvent(
        type="turn_commit_request",
        session_id="session-1",
        turn_id="turn-1",
        seq=1,
        trace_id="trace-1",
        timestamp_ms=1715670000000,
        reason="算力平台试用期限多久？",
        input_mode="text",
    )
    assert event.input_mode == "text"


def test_audio_chunk_event_has_defaults() -> None:
    event = AudioChunkEvent(
        type="audio_chunk",
        session_id="session-1",
        turn_id="turn-1",
        seq=2,
        trace_id="trace-1",
        timestamp_ms=1715670000100,
        audio_base64="AA==",
    )
    assert event.codec == "pcm_s16le"
    assert event.sample_rate_hz == 16000


def test_vad_event_accepts_speech_start() -> None:
    event = VadEvent(
        type="vad_event",
        session_id="session-1",
        turn_id="turn-1",
        seq=4,
        trace_id="trace-1",
        timestamp_ms=1715670000300,
        event="speech_start",
    )
    assert event.event == "speech_start"


def test_cancel_event_requires_generation_id() -> None:
    with pytest.raises(ValidationError):
        CancelEvent(
            type="cancel",
            session_id="session-1",
            turn_id="turn-1",
            seq=3,
            trace_id="trace-1",
            timestamp_ms=1715670000200,
        )
