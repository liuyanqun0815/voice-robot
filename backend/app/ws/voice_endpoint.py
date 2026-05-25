import asyncio
import base64
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.metrics import WS_CONNECTIONS_ACTIVE
from app.schemas.events import AudioChunkEvent, CancelEvent, TurnCommitRequestEvent, VadEvent
from app.services.asr.tencent_ws_client import TencentAsrClient
from app.services.orchestrator import Orchestrator
from app.services.session_manager import SessionManager
from app.services.turn_manager import TurnManager

router = APIRouter()
logger = logging.getLogger(__name__)
turn_manager = TurnManager()
session_manager = SessionManager()
asr_client = TencentAsrClient()


@router.websocket("/ws/voice")
async def voice_ws(websocket: WebSocket) -> None:
    # 设计说明：
    # - 一个 WebSocket 连接承载一个前端会话。
    # - 连接内可交织：vad_event / audio_chunk / turn_commit_request / cancel。
    # - speech_start 时预建腾讯 ASR WebSocket，避免首包音频与建连竞态及 15 秒空闲超时。
    await websocket.accept()
    WS_CONNECTIONS_ACTIVE.inc()
    logger.info("voice websocket connected")
    asr_event_buffer: list[dict] = []
    latest_turn_by_session: dict[str, str] = {}

    async def flush_asr_events() -> None:
        while asr_event_buffer:
            await websocket.send_json(asr_event_buffer.pop(0))

    def _turn_id_for(session_id: str, fallback: str) -> str:
        return latest_turn_by_session.get(session_id, fallback)

    async def ensure_asr_connected(session_id: str, turn_id: str) -> None:
        latest_turn_by_session[session_id] = turn_id

        def _append_asr_event(event: dict) -> None:
            asr_event_buffer.append(event)

        await asr_client.connect(
            session_id,
            on_partial=lambda text, sid=session_id, tid=turn_id: _append_asr_event(
                {
                    "type": "asr_partial",
                    "session_id": sid,
                    "turn_id": _turn_id_for(sid, tid),
                    "text": text,
                }
            ),
            on_final=lambda text, sid=session_id, tid=turn_id: _append_asr_event(
                {
                    "type": "asr_final",
                    "session_id": sid,
                    "turn_id": _turn_id_for(sid, tid),
                    "text": text,
                    "is_final": True,
                }
            ),
            on_error=lambda text, sid=session_id, tid=turn_id: _append_asr_event(
                {
                    "type": "error",
                    "session_id": sid,
                    "turn_id": _turn_id_for(sid, tid),
                    "error_code": "ASR_STREAM_ERROR",
                    "message": text,
                }
            ),
        )

    orchestrator = Orchestrator(send_event=websocket.send_json, turn_manager=turn_manager)
    active_turn_tasks: dict[tuple[str, str], asyncio.Task[str]] = {}
    greeting_task: asyncio.Task[None] | None = None
    greeting_dispatched = False

    def _log_turn_task_result(task: asyncio.Task[str], session_id: str, turn_id: str) -> None:
        if task.cancelled():
            logger.info("turn task cancelled: session_id=%s turn_id=%s", session_id, turn_id)
            return
        exc = task.exception()
        if exc is not None:
            logger.exception(
                "turn task failed: session_id=%s turn_id=%s",
                session_id,
                turn_id,
                exc_info=exc,
            )

    try:
        while True:
            payload = await websocket.receive_json()
            event_type = payload.get("type", "")
            try:
                if event_type == "session_init":
                    session_id = str(payload.get("session_id", "")).strip()
                    if session_id and not greeting_dispatched:
                        greeting_dispatched = True
                        session_manager.get_or_create(session_id)
                        greeting_task = asyncio.create_task(
                            orchestrator.send_session_greeting(session_id),
                            name=f"session_greeting:{session_id}",
                        )
                    continue

                if event_type == "vad_event":
                    event = VadEvent.model_validate(payload)
                    latest_turn_by_session[event.session_id] = event.turn_id
                    if event.event == "speech_start":
                        logger.info(
                            "vad speech_start, pre-connect asr: session_id=%s turn_id=%s",
                            event.session_id,
                            event.turn_id,
                        )
                        await ensure_asr_connected(event.session_id, event.turn_id)
                        await flush_asr_events()
                    elif event.event == "speech_end":
                        logger.info(
                            "vad speech_end, close asr: session_id=%s turn_id=%s",
                            event.session_id,
                            event.turn_id,
                        )
                        await asr_client.close(event.session_id)
                        await flush_asr_events()
                    continue

                if event_type == "audio_chunk":
                    event = AudioChunkEvent.model_validate(payload)
                    latest_turn_by_session[event.session_id] = event.turn_id
                    await ensure_asr_connected(event.session_id, event.turn_id)
                    audio_bytes = base64.b64decode(event.audio_base64.encode("ascii"), validate=False)
                    await asr_client.append_audio(event.session_id, audio_bytes)
                    await flush_asr_events()
                    continue

                if event_type == "cancel":
                    event = CancelEvent.model_validate(payload)
                    cancelled = turn_manager.cancel_generation(event.session_id, event.turn_id, event.generation_id)
                    logger.info(
                        "cancel event: session_id=%s turn_id=%s cancelled=%s",
                        event.session_id,
                        event.turn_id,
                        cancelled,
                    )
                    if cancelled:
                        session_manager.set_status(event.session_id, "interrupted")
                    await websocket.send_json(
                        {
                            "type": "cancel_ack",
                            "session_id": event.session_id,
                            "turn_id": event.turn_id,
                            "generation_id": event.generation_id,
                            "cancelled": cancelled,
                        }
                    )
                    continue

                if event_type != "turn_commit_request":
                    await websocket.send_json({"type": "error", "error_code": "INVALID_EVENT"})
                    continue

                event = TurnCommitRequestEvent.model_validate(payload)
                user_text = event.reason.strip()
                if not user_text:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "session_id": event.session_id,
                            "turn_id": event.turn_id,
                            "error_code": "EMPTY_USER_TEXT",
                            "message": "提交内容不能为空",
                        }
                    )
                    continue

                session_manager.get_or_create(event.session_id)
                committed = turn_manager.commit_turn_once(event.session_id, event.turn_id)

                if not committed:
                    logger.info(
                        "turn rejected(already committed): session_id=%s turn_id=%s",
                        event.session_id,
                        event.turn_id,
                    )
                    await websocket.send_json(
                        {
                            "type": "turn_rejected",
                            "session_id": event.session_id,
                            "turn_id": event.turn_id,
                            "error_code": "ALREADY_COMMITTED",
                        }
                    )
                    continue

                session_manager.set_status(event.session_id, "thinking")
                generation_id = turn_manager.get_generation_id(event.session_id, event.turn_id)
                logger.info(
                    "turn committed: session_id=%s turn_id=%s generation_id=%s input_mode=%s",
                    event.session_id,
                    event.turn_id,
                    generation_id,
                    event.input_mode,
                )
                await websocket.send_json(
                    {
                        "type": "turn_committed",
                        "session_id": event.session_id,
                        "turn_id": event.turn_id,
                        "generation_id": generation_id,
                    }
                )

                turn_key = (event.session_id, event.turn_id)
                prev_task = active_turn_tasks.pop(turn_key, None)
                if prev_task is not None and not prev_task.done():
                    prev_task.cancel()

                async def _run_turn_bg(
                    sid: str = event.session_id,
                    tid: str = event.turn_id,
                    gid: str = generation_id,
                    text: str = user_text,
                    trace: str = event.trace_id,
                    mode: str = event.input_mode,
                ) -> str:
                    return await orchestrator.run_turn(
                        sid,
                        tid,
                        text,
                        generation_id=gid,
                        trace_id=trace,
                        input_mode=mode,
                    )

                turn_task = asyncio.create_task(_run_turn_bg(), name=f"turn:{event.session_id}:{event.turn_id}")

                def _on_turn_done(task: asyncio.Task[str], sid: str = event.session_id, tid: str = event.turn_id) -> None:
                    active_turn_tasks.pop((sid, tid), None)
                    _log_turn_task_result(task, sid, tid)
                    try:
                        outcome = task.result()
                    except Exception:
                        session_manager.set_status(sid, "listening")
                        return
                    if outcome == "cancelled":
                        session_manager.set_status(sid, "interrupted")
                    else:
                        session_manager.set_status(sid, "listening")

                turn_task.add_done_callback(_on_turn_done)
                active_turn_tasks[turn_key] = turn_task
            except Exception as exc:
                logger.exception("voice event processing failed: event_type=%s", event_type)
                await websocket.send_json(
                    {
                        "type": "error",
                        "error_code": "PROCESSING_FAILED",
                        "message": str(exc),
                    }
                )
    except WebSocketDisconnect:
        logger.info("voice websocket disconnected")
        return
    finally:
        if greeting_task is not None and not greeting_task.done():
            greeting_task.cancel()
        for task in list(active_turn_tasks.values()):
            if not task.done():
                task.cancel()
        WS_CONNECTIONS_ACTIVE.dec()
