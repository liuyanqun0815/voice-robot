from collections.abc import Callable
from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any
import sys

from app.core.settings import Settings


@dataclass
class _AsrSession:
    session_id: str
    is_ready: bool = False
    is_connecting: bool = False
    bytes_received: int = 0
    pending_audio: list[bytes] = field(default_factory=list)
    writer: Callable[[bytes], None] | None = None
    stopper: Callable[[], None] | None = None
    on_partial: Callable[[str], None] | None = None
    on_final: Callable[[str], None] | None = None
    on_error: Callable[[str], None] | None = None


class TencentAsrClient:
    """Tencent realtime ASR adapter.

    实现策略：
    - 生产模式：接入腾讯 SDK 的 SpeechRecognizer（参考官方 asrexample.py）
    - 开发/测试模式：启用 mock，不依赖腾讯 SDK 也能跑通流程

    关键约束：
    - append_audio 只接收 16k / int16 PCM 字节流。
    - connect 后未 ready 前禁止写音频。
    - 回调里要尽量提取纯文本，避免把完整 JSON 透传到前端。
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._logger = logging.getLogger(__name__)
        self._settings = settings or Settings()
        self._sessions: dict[str, _AsrSession] = {}

    async def connect(
        self,
        session_id: str,
        on_partial: Callable[[str], None] | None = None,
        on_final: Callable[[str], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        # 每个 session_id 对应一轮 ASR 会话；speech_end 后应 close，下次 speech_start 再建连。
        existing = self._sessions.get(session_id)
        if existing is not None:
            if existing.is_ready or existing.is_connecting:
                return
            self._sessions.pop(session_id, None)

        session = _AsrSession(
            session_id=session_id,
            is_ready=self._settings.mock_streaming_enabled,
            is_connecting=not self._settings.mock_streaming_enabled,
            on_partial=on_partial,
            on_final=on_final,
            on_error=on_error,
        )
        self._sessions[session_id] = session
        self._logger.info("asr connect: session_id=%s mock=%s", session_id, self._settings.mock_streaming_enabled)
        if not self._settings.mock_streaming_enabled:
            self._connect_live_sdk(session)

    def is_ready(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        return bool(session and session.is_ready)

    def _write_audio(self, session: _AsrSession, audio_bytes: bytes) -> None:
        session.bytes_received += len(audio_bytes)
        if session.bytes_received % (3200 * 20) == 0:
            # 每约4秒打印一次累计量，避免日志刷屏。
            self._logger.debug(
                "asr audio buffered: session_id=%s bytes=%s",
                session.session_id,
                session.bytes_received,
            )
        if self._settings.mock_streaming_enabled and session.on_partial is not None:
            # mock 模式下构造“可见进度”，便于前端联调。
            chunk_count = max(1, session.bytes_received // 3200)
            session.on_partial(f"正在识别语音...({chunk_count})")
        if session.writer is not None:
            session.writer(audio_bytes)

    def _flush_pending_audio(self, session: _AsrSession) -> None:
        if not session.pending_audio:
            return
        pending = session.pending_audio
        session.pending_audio = []
        for chunk in pending:
            self._write_audio(session, chunk)

    async def append_audio(self, session_id: str, audio_bytes: bytes) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            raise RuntimeError("ASR session not found")
        if not session.is_ready:
            session.pending_audio.append(audio_bytes)
            return
        self._write_audio(session, audio_bytes)

    async def close(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        self._logger.info("asr close: session_id=%s", session_id)
        if session and session.stopper is not None:
            session.stopper()

    def register_mock_writer(self, session_id: str, writer: Callable[[bytes], None]) -> None:
        """测试注入：用于验证 append_audio 调用行为。"""
        session = self._sessions.setdefault(session_id, _AsrSession(session_id=session_id, is_ready=True))
        session.writer = writer

    def _connect_live_sdk(self, session: _AsrSession) -> None:
        """参考腾讯官方示例接入 SpeechRecognizer。

        文档/示例：
        - https://github.com/TencentCloud/tencentcloud-speech-sdk-python/blob/master/examples/asr/asrexample.py
        """
        sdk_root = Path(__file__).resolve().parents[3] / "vendor" / "tencentcloud-speech-sdk-python"
        if sdk_root.exists():
            sdk_path = str(sdk_root)
            if sdk_path not in sys.path:
                sys.path.insert(0, sdk_path)
        self._logger.info("asr live sdk path: %s", sdk_root)

        try:
            from asr import speech_recognizer  # type: ignore[import-not-found]
            from common import credential  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "Tencent speech SDK not found. Install TencentCloud speech sdk and ensure "
                "`common.credential` and `asr.speech_recognizer` are importable. "
                f"Also checked vendor path: {sdk_root}"
            ) from exc

        def _format_asr_fail_message(response: dict[str, Any]) -> str:
            for key in ("message", "msg", "err_msg", "error_msg"):
                value = response.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            return str(response)

        def _extract_text(response: dict[str, Any]) -> str:
            # 腾讯实时结果可能在顶层 voice_text_str，也可能嵌套在 result 内。
            # 这里统一抽取成“纯文本”，前端不需要关心 SDK 返回结构。
            if "voice_text_str" in response and isinstance(response["voice_text_str"], str):
                return response["voice_text_str"]

            result = response.get("result", "")
            if isinstance(result, str):
                return result
            if isinstance(result, dict):
                if isinstance(result.get("voice_text_str"), str):
                    return str(result["voice_text_str"])
                return str(result)
            if isinstance(result, list):
                parts: list[str] = []
                for item in result:
                    if isinstance(item, dict) and isinstance(item.get("voice_text_str"), str):
                        parts.append(str(item["voice_text_str"]))
                if parts:
                    return "".join(parts)
                return str(result)
            return str(result)

        class _Listener(speech_recognizer.SpeechRecognitionListener):
            def __init__(self, outer_session: _AsrSession, client: TencentAsrClient) -> None:
                self._session = outer_session
                self._client = client

            def on_recognition_start(self, response: dict[str, Any]) -> None:  # noqa: ANN001
                _ = response
                # SDK 通知“识别会话可写”。
                self._session.is_connecting = False
                self._session.is_ready = True
                self._client._flush_pending_audio(self._session)

            def on_recognition_result_change(self, response: dict[str, Any]) -> None:  # noqa: ANN001
                if self._session.on_partial is None:
                    return
                # partial 用于前端实时字幕（草稿态）。
                text = _extract_text(response)
                if text:
                    self._session.on_partial(text)

            def on_sentence_end(self, response: dict[str, Any]) -> None:  # noqa: ANN001
                if self._session.on_final is None:
                    return
                # final 用于本轮最终提交依据。
                text = _extract_text(response)
                if text:
                    self._session.on_final(text)

            def on_fail(self, response: dict[str, Any]) -> None:  # noqa: ANN001
                if self._session.on_error is None:
                    return
                message = _format_asr_fail_message(response)
                self._session.on_error(message)

        listener = _Listener(session, self)
        credential_var = credential.Credential(
            self._settings.tencent_asr_secret_id.get_secret_value(),
            self._settings.tencent_asr_secret_key.get_secret_value(),
        )
        recognizer = speech_recognizer.SpeechRecognizer(
            self._settings.tencent_asr_app_id,
            credential_var,
            self._settings.tencent_asr_engine_model_type,
            listener,
        )
        recognizer.set_filter_modal(1)
        recognizer.set_filter_punc(1)
        recognizer.set_filter_dirty(1)
        recognizer.set_need_vad(self._settings.tencent_asr_need_vad)
        recognizer.set_vad_silence_time(self._settings.tencent_asr_vad_silence_time_ms)
        recognizer.set_noise_threshold(self._settings.tencent_asr_noise_threshold)
        recognizer.set_voice_format(1)
        recognizer.start()
        self._logger.info("asr recognizer started: session_id=%s", session.session_id)

        session.writer = recognizer.write
        session.stopper = recognizer.stop
