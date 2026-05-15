import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.main import app


def test_ws_accepts_turn_commit_request() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json(
            {
                "type": "turn_commit_request",
                "session_id": "s1",
                "turn_id": "t1",
                "seq": 1,
                "trace_id": "tr1",
                "timestamp_ms": 1,
                "reason": "frontend_vad_end",
            }
        )
        message = ws.receive_json()
        assert message["type"] == "turn_committed"
        assert message["generation_id"] == "g_t1"

        event_types: list[str] = []
        for _ in range(500):
            event = ws.receive_json()
            event_types.append(event["type"])
            if event["type"] == "audio_complete":
                break

        assert "llm_delta" in event_types
        assert "tts_chunk" in event_types
        assert "audio_complete" in event_types


def test_ws_vad_speech_start_preconnects_asr() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json(
            {
                "type": "vad_event",
                "session_id": "s_vad",
                "turn_id": "t_vad",
                "seq": 1,
                "trace_id": "tr_vad",
                "timestamp_ms": 1,
                "event": "speech_start",
            }
        )
        ws.send_json(
            {
                "type": "audio_chunk",
                "session_id": "s_vad",
                "turn_id": "t_vad",
                "seq": 2,
                "trace_id": "tr_vad",
                "timestamp_ms": 2,
                "audio_base64": "AA==",
            }
        )
        ws.send_json(
            {
                "type": "vad_event",
                "session_id": "s_vad",
                "turn_id": "t_vad",
                "seq": 3,
                "trace_id": "tr_vad",
                "timestamp_ms": 3,
                "event": "speech_end",
            }
        )
        # 预连接 + 推流 + 关闭流程应无异常（mock 下可能异步下发 asr_partial，此处不阻塞等待）


def test_ws_cancel_acknowledged() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json(
            {
                "type": "turn_commit_request",
                "session_id": "s2",
                "turn_id": "t2",
                "seq": 1,
                "trace_id": "tr2",
                "timestamp_ms": 2,
                "reason": "frontend_vad_end",
            }
        )
        ws.send_json(
            {
                "type": "cancel",
                "session_id": "s2",
                "turn_id": "t2",
                "seq": 2,
                "trace_id": "tr2",
                "timestamp_ms": 3,
                "generation_id": "g_t2",
                "reason": "barge_in",
            }
        )
        for _ in range(300):
            message = ws.receive_json()
            if message["type"] == "cancel_ack":
                assert message["cancelled"] is True
                break
        else:
            raise AssertionError("cancel_ack was not received")
