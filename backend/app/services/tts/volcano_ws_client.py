"""火山引擎豆包语音 v3 双向流式 TTS（与官方 `bidirection.py` demo 对齐）。

- mock：`VOICE_ROBOT_MOCK_STREAMING_ENABLED=true` 时返回 UTF-8 占位字节。
- live：`wss://openspeech.bytedance.com/api/v3/tts/bidirection`，握手头 `X-Api-*`，帧格式见 `protocols.py`。
"""

from __future__ import annotations

import copy
import json
import logging
import uuid
from collections.abc import AsyncIterator
from enum import IntEnum
from typing import Any

from app.core.settings import Settings

try:
    import websockets
except Exception:  # pragma: no cover
    websockets = None  # type: ignore[assignment]

from app.services.tts.protocols import (
    EventType,
    MsgType,
    finish_connection,
    finish_session,
    receive_message,
    start_connection,
    start_session,
    task_request,
    wait_for_event,
)

logger = logging.getLogger(__name__)


def resource_id_for_voice(voice: str) -> str:
    """与官方 demo `get_resource_id` 一致。"""
    if voice.startswith("S_"):
        return "volc.megatts.default"
    return "volc.service_type.10029"


def _json_payload(obj: dict) -> bytes:
    """将请求体序列化为 JSON；事件枚举以整型写入。"""

    def _default(o: object) -> int:
        if isinstance(o, IntEnum):
            return int(o)
        raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")

    return json.dumps(obj, ensure_ascii=False, default=_default).encode("utf-8")


class VolcanoTtsClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()
        self.ws_url = self._settings.volcano_tts_ws_url

    def _build_base_request(self) -> dict:
        return {
            "user": {"uid": str(uuid.uuid4())},
            "namespace": "BidirectionalTTS",
            "req_params": {
                "speaker": self._settings.volcano_tts_voice_type,
                "audio_params": {
                    "format": self._settings.volcano_tts_audio_format,
                    "sample_rate": self._settings.volcano_tts_sample_rate,
                    "enable_timestamp": False,
                },
                "additions": json.dumps({"disable_markdown_filter": False}),
            },
        }

    async def stream_sentence(self, text: str) -> AsyncIterator[bytes]:
        if self._settings.mock_streaming_enabled:
            payload = text.encode("utf-8")
            yield payload if payload else b"fake_audio_chunk"
            return

        if websockets is None:
            raise RuntimeError("请安装 websockets 依赖以使用火山双向流式 TTS")

        stripped = (text or "").strip()
        if not stripped:
            yield b"fake_audio_chunk"
            return

        app_id = (self._settings.volcano_tts_app_id or "").strip()
        token = self._settings.volcano_tts_access_token.get_secret_value()
        if not app_id or not token:
            raise RuntimeError("缺少 VOICE_ROBOT_VOLCANO_TTS_APP_ID 或 VOICE_ROBOT_VOLCANO_TTS_ACCESS_TOKEN")

        rid = (self._settings.volcano_tts_resource_id or "").strip() or resource_id_for_voice(
            self._settings.volcano_tts_voice_type
        )
        headers = {
            "X-Api-App-Key": app_id,
            "X-Api-Access-Key": token,
            "X-Api-Resource-Id": rid,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }

        logger.info(
            "volcano tts bidirection: url=%s resource_id=%s voice=%s text_len=%s",
            self._settings.volcano_tts_ws_url,
            rid,
            self._settings.volcano_tts_voice_type,
            len(stripped),
        )

        try:
            ws_cm = websockets.connect(
                self._settings.volcano_tts_ws_url,
                additional_headers=headers,
                max_size=10 * 1024 * 1024,
            )
        except Exception as exc:
            raise RuntimeError(f"火山 TTS 连接初始化失败: {exc}") from exc

        try:
            async with ws_cm as ws:
                async for chunk in self._synthesize_on_ws(ws, stripped):
                    yield chunk
        except Exception as exc:
            err_text = str(exc)
            if "403" in err_text or "rejected WebSocket" in err_text:
                raise RuntimeError(
                    "火山 TTS 鉴权失败(HTTP 403)：请核对控制台 APP_ID、Access Token 是否对应「双向流式」"
                    f"服务，resource_id={rid}，音色={self._settings.volcano_tts_voice_type}。"
                    "可在 .env 设置 VOICE_ROBOT_VOLCANO_TTS_RESOURCE_ID（控制台资源 ID）。"
                ) from exc
            raise

    async def _synthesize_on_ws(self, ws: Any, stripped: str) -> AsyncIterator[bytes]:
        await start_connection(ws)
        await wait_for_event(ws, MsgType.FullServerResponse, EventType.ConnectionStarted)

        base_request = self._build_base_request()
        session_id = str(uuid.uuid4())

        start_session_request = copy.deepcopy(base_request)
        start_session_request["event"] = EventType.StartSession
        await start_session(ws, _json_payload(start_session_request), session_id)
        await wait_for_event(ws, MsgType.FullServerResponse, EventType.SessionStarted)

        synthesis_request = copy.deepcopy(base_request)
        synthesis_request["event"] = EventType.TaskRequest
        synthesis_request["req_params"]["text"] = stripped
        await task_request(ws, _json_payload(synthesis_request), session_id)
        await finish_session(ws, session_id)

        while True:
            msg = await receive_message(ws)
            if msg.type == MsgType.FullServerResponse and msg.event == EventType.SessionFinished:
                break
            if msg.type == MsgType.AudioOnlyServer and msg.payload:
                yield bytes(msg.payload)
            elif msg.type == MsgType.Error:
                raise RuntimeError(msg.payload.decode("utf-8", errors="replace"))
            elif msg.type == MsgType.FullServerResponse and msg.event == EventType.SessionFailed:
                raise RuntimeError(msg.payload.decode("utf-8", errors="replace"))

        await finish_connection(ws)
        await wait_for_event(ws, MsgType.FullServerResponse, EventType.ConnectionFinished)
