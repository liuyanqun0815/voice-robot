import asyncio
import base64
import contextvars
import inspect
import logging
import time
from collections.abc import AsyncIterator, Callable, Iterator

from langchain_core.messages import AIMessage, HumanMessage

from app.core.metrics import LLM_FIRST_TOKEN_SECONDS, TURN_DURATION_SECONDS, TURN_TOTAL
from app.core.request_context import bind_voice_context, reset_wiki_audit
from app.core.settings import Settings
from app.services.agents.deepagent_runner import DeepAgentRunner
from app.services.audit_service import get_audit_service
from app.services.turn_manager import TurnManager
from app.services.user_content_enricher import enrich_user_text_for_agent
from app.services.text_punctuation_buffer import PunctuationStreamBuffer
from app.services.tts.volcano_ws_client import VolcanoTtsClient

GREETING_TURN_ID = "__greeting__"


async def async_iter_sync_strings(
    sync_gen: Iterator[str],
    *,
    should_continue: Callable[[], bool] | None = None,
) -> AsyncIterator[str]:
    """在默认线程池中迭代同步生成器，避免阻塞事件循环。

    使用 copy_context，使线程池内 DeepAgent / query_kefu_wiki 能写回 ContextVar（审计 tool_called）。
    """
    loop = asyncio.get_running_loop()
    it = iter(sync_gen)
    ctx = contextvars.copy_context()

    def _pull() -> str | None:
        return ctx.run(_pull_next)

    def _pull_next() -> str | None:
        try:
            return next(it)
        except StopIteration:
            return None

    while True:
        if should_continue is not None and not should_continue():
            break
        item = await loop.run_in_executor(None, _pull)
        if item is None:
            break
        yield item


class Orchestrator:
    def __init__(
        self,
        send_event: Callable[..., object],
        *,
        turn_manager: TurnManager | None = None,
    ) -> None:
        self._logger = logging.getLogger(__name__)
        self._send_event = send_event
        self._turn_manager = turn_manager
        self._settings = Settings()
        self._agent_runner = DeepAgentRunner(self._settings)
        self._tts = VolcanoTtsClient()

    def _generation_alive(self, session_id: str, turn_id: str, generation_id: str) -> bool:
        if not generation_id or self._turn_manager is None:
            return bool(generation_id) or self._turn_manager is None
        return self._turn_manager.is_generation_active(session_id, turn_id, generation_id)

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

    async def _stream_tts_for_sentence(
        self,
        session_id: str,
        turn_id: str,
        generation_id: str,
        sentence: str,
        chunk_index: int,
        *,
        skip_cancel_check: bool = False,
    ) -> int:
        if not skip_cancel_check and not self._generation_alive(session_id, turn_id, generation_id):
            return chunk_index
        try:
            async for chunk in self._tts.stream_sentence(sentence):
                if not skip_cancel_check and not self._generation_alive(session_id, turn_id, generation_id):
                    break
                await self._emit(
                    {
                        "type": "tts_chunk",
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "generation_id": generation_id,
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

    async def _stream_greeting_text(self, session_id: str, text: str) -> None:
        chunk_size = max(1, self._settings.greeting_stream_chunk_chars)
        interval_s = max(0, self._settings.greeting_stream_interval_ms) / 1000.0
        for offset in range(0, len(text), chunk_size):
            piece = text[offset : offset + chunk_size]
            await self._emit(
                {
                    "type": "greeting_delta",
                    "session_id": session_id,
                    "turn_id": GREETING_TURN_ID,
                    "text": piece,
                }
            )
            if interval_s > 0 and offset + chunk_size < len(text):
                await asyncio.sleep(interval_s)
        await self._emit(
            {
                "type": "greeting_complete",
                "session_id": session_id,
                "turn_id": GREETING_TURN_ID,
            }
        )

    async def send_session_greeting(self, session_id: str = "") -> None:
        """连接建立后推送可配置开场白（流式文本 + 可选 TTS），不占用正式 turn 提交名额。"""
        text = self._settings.greeting_text.strip()
        if not self._settings.greeting_enabled or not text:
            return
        self._logger.info("session greeting: session_id=%s chars=%s", session_id or "-", len(text))
        await self._stream_greeting_text(session_id, text)
        if not self._settings.tts_enabled:
            return
        punct_buf = PunctuationStreamBuffer()
        chunk_index = 0
        for sentence in punct_buf.feed(text):
            chunk_index = await self._stream_tts_for_sentence(
                session_id,
                GREETING_TURN_ID,
                "",
                sentence,
                chunk_index,
                skip_cancel_check=True,
            )
        tail = punct_buf.drain()
        if tail:
            chunk_index = await self._stream_tts_for_sentence(
                session_id,
                GREETING_TURN_ID,
                "",
                tail,
                chunk_index,
                skip_cancel_check=True,
            )
        await self._emit(
            {
                "type": "audio_complete",
                "session_id": session_id,
                "turn_id": GREETING_TURN_ID,
            }
        )

    async def run_turn(
        self,
        session_id: str,
        turn_id: str,
        user_text: str,
        *,
        generation_id: str = "",
        trace_id: str = "",
        input_mode: str = "voice",
    ) -> str:
        """DeepAgent 流式文本 → 标点切段 → 可选分段流式 TTS → WebSocket 下发。

        返回 ``ok`` / ``cancelled`` / ``error``。cancel 后不再向客户端推送 llm/tts 事件。
        """
        bind_voice_context(
            trace_id=trace_id,
            session_id=session_id,
            turn_id=turn_id,
            input_mode=input_mode,
        )
        reset_wiki_audit()
        self._logger.info(
            "run turn: session_id=%s turn_id=%s trace_id=%s input_mode=%s",
            session_id,
            turn_id,
            trace_id,
            input_mode,
        )

        started = time.perf_counter()
        assistant_parts: list[str] = []
        status = "ok"
        result = "ok"
        error_code: str | None = None
        punct_buf = PunctuationStreamBuffer()
        chunk_index = 0
        should_continue = lambda: self._generation_alive(session_id, turn_id, generation_id)
        history_before_turn = self._agent_runner.get_thread_messages(session_id)

        agent_input = await enrich_user_text_for_agent(user_text, self._settings)
        if agent_input != user_text:
            self._logger.info(
                "user text enriched with links: session_id=%s turn_id=%s extra_chars=%s",
                session_id,
                turn_id,
                len(agent_input) - len(user_text),
            )

        llm_started = time.perf_counter()
        llm_first_token_ms: int | None = None
        sync_gen = self._agent_runner.stream_assistant_text(agent_input, thread_id=session_id)

        try:
            async for delta in async_iter_sync_strings(sync_gen, should_continue=should_continue):
                if not should_continue():
                    result = "cancelled"
                    status = "cancelled"
                    break
                if llm_first_token_ms is None:
                    llm_first_token_ms = int((time.perf_counter() - llm_started) * 1000)
                    LLM_FIRST_TOKEN_SECONDS.labels(input_mode=input_mode).observe(llm_first_token_ms / 1000.0)
                assistant_parts.append(delta)
                await self._emit(
                    {
                        "type": "llm_delta",
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "generation_id": generation_id,
                        "text": delta,
                    }
                )
                if not self._settings.tts_enabled:
                    continue
                for sentence in punct_buf.feed(delta):
                    if not should_continue():
                        result = "cancelled"
                        status = "cancelled"
                        break
                    chunk_index = await self._stream_tts_for_sentence(
                        session_id, turn_id, generation_id, sentence, chunk_index
                    )
                if result == "cancelled":
                    break

            if result != "cancelled" and not should_continue():
                result = "cancelled"
                status = "cancelled"

            if result != "cancelled" and should_continue() and self._settings.tts_enabled:
                tail = punct_buf.drain()
                if tail:
                    chunk_index = await self._stream_tts_for_sentence(
                        session_id, turn_id, generation_id, tail, chunk_index
                    )

            if result != "cancelled" and should_continue():
                await self._emit(
                    {
                        "type": "audio_complete",
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "generation_id": generation_id,
                    }
                )
            elif result == "cancelled":
                self._logger.info(
                    "turn cancelled, stop streaming: session_id=%s turn_id=%s generation_id=%s",
                    session_id,
                    turn_id,
                    generation_id,
                )
        except Exception:
            status = "error"
            result = "error"
            error_code = "ORCHESTRATOR_FAILED"
            raise
        finally:
            if result == "cancelled":
                self._agent_runner.close_stream(sync_gen)
                partial_text = "".join(assistant_parts).strip()
                patched_messages = [*history_before_turn, HumanMessage(content=user_text)]
                if partial_text:
                    patched_messages.append(AIMessage(content=partial_text))
                self._agent_runner.replace_thread_messages(session_id, patched_messages)
            latency_ms = int((time.perf_counter() - started) * 1000)
            assistant_text = "".join(assistant_parts)
            TURN_DURATION_SECONDS.labels(stage="e2e").observe(latency_ms / 1000.0)
            TURN_TOTAL.labels(status=status, input_mode=input_mode).inc()
            audit = get_audit_service()
            if audit is not None:
                audit.record_turn(
                    trace_id=trace_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    input_mode=input_mode,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    latency_ms_e2e=latency_ms,
                    llm_first_token_ms=llm_first_token_ms or 0,
                    status=status,
                    error_code=error_code,
                )
        return result
