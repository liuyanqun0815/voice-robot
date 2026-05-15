import asyncio
import base64
import inspect
import logging
from collections.abc import AsyncIterator, Callable, Iterator

from app.services.agents.deepagent_runner import DeepAgentRunner
from app.services.text_punctuation_buffer import PunctuationStreamBuffer
from app.services.tts.volcano_ws_client import VolcanoTtsClient


async def async_iter_sync_strings(sync_gen: Iterator[str]) -> AsyncIterator[str]:
    """在默认线程池中迭代同步生成器，避免阻塞事件循环。"""
    loop = asyncio.get_running_loop()
    it = iter(sync_gen)

    def _pull() -> str | None:
        try:
            return next(it)
        except StopIteration:
            return None

    while True:
        item = await loop.run_in_executor(None, _pull)
        if item is None:
            break
        yield item


class Orchestrator:
    def __init__(self, send_event: Callable[..., object]) -> None:
        self._logger = logging.getLogger(__name__)
        self._send_event = send_event
        self._agent_runner = DeepAgentRunner()
        self._tts = VolcanoTtsClient()

    async def _emit(self, event: dict) -> None:
        result = self._send_event(event)
        if inspect.isawaitable(result):
            await result

    def on_asr_partial(self, session_id: str, turn_id: str, text: str) -> None:
        self._send_event(
            {
                "type": "asr_partial",
                "session_id": session_id,
                "turn_id": turn_id,
                "text": text,
            }
        )

    async def _stream_tts_for_sentence(self, session_id: str, turn_id: str, sentence: str, chunk_index: int) -> int:
        try:
            async for chunk in self._tts.stream_sentence(sentence):
                await self._emit(
                    {
                        "type": "tts_chunk",
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "chunk_index": chunk_index,
                        "audio_base64": base64.b64encode(chunk).decode("ascii"),
                    }
                )
                chunk_index += 1
        except Exception as exc:
            self._logger.warning("tts stream failed, fallback to text: %s", exc)
            await self._emit(
                {
                    "type": "tts_error",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "message": str(exc),
                }
            )
        return chunk_index

    async def run_turn(self, session_id: str, turn_id: str, user_text: str) -> None:
        """DeepAgent 流式文本 → 标点切段 → 分段流式 TTS → WebSocket 下发。"""
        self._logger.info("run turn: session_id=%s turn_id=%s", session_id, turn_id)
        punct_buf = PunctuationStreamBuffer()
        chunk_index = 0
        thread_id = f"{session_id}:{turn_id}"
        sync_gen = self._agent_runner.stream_assistant_text(user_text, thread_id=thread_id)

        async for delta in async_iter_sync_strings(sync_gen):
            self._logger.info(f"----------delta:{delta}--------------")
            await self._emit(
                {
                    "type": "llm_delta",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "text": delta,
                }
            )
            for sentence in punct_buf.feed(delta):
                chunk_index = await self._stream_tts_for_sentence(session_id, turn_id, sentence, chunk_index)

        tail = punct_buf.drain()
        if tail:
            chunk_index = await self._stream_tts_for_sentence(session_id, turn_id, tail, chunk_index)

        await self._emit(
            {
                "type": "audio_complete",
                "session_id": session_id,
                "turn_id": turn_id,
            }
        )
