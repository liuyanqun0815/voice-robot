import base64
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

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

    orchestrator = Orchestrator(send_event=websocket.send_json)
    try:
        while True:
            payload = await websocket.receive_json()
            event_type = payload.get("type", "")
            try:
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
                    session_manager.set_status(event.session_id, "interrupted")
                    await websocket.send_json(
                        {
                            "type": "cancel_ack",
                            "session_id": event.session_id,
                            "turn_id": event.turn_id,
                            "cancelled": cancelled,
                        }
                    )
                    continue

                if event_type != "turn_commit_request":
                    await websocket.send_json({"type": "error", "error_code": "INVALID_EVENT"})
                    continue

                event = TurnCommitRequestEvent.model_validate(payload)
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
                    "turn committed: session_id=%s turn_id=%s generation_id=%s",
                    event.session_id,
                    event.turn_id,
                    generation_id,
                )
                await websocket.send_json(
                    {
                        "type": "turn_committed",
                        "session_id": event.session_id,
                        "turn_id": event.turn_id,
                        "generation_id": generation_id,
                    }
                )
                await orchestrator.run_turn(event.session_id, event.turn_id, event.reason)
                session_manager.set_status(event.session_id, "speaking")
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
