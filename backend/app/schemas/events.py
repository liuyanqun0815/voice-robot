from typing import Literal

from pydantic import BaseModel, Field

VadEventName = Literal["speech_start", "speech_end"]


class BaseVoiceEvent(BaseModel):
    type: str
    session_id: str
    turn_id: str
    seq: int = Field(ge=0)
    trace_id: str
    timestamp_ms: int = Field(ge=0)


class TurnCommitRequestEvent(BaseVoiceEvent):
    type: Literal["turn_commit_request"] = "turn_commit_request"
    reason: str


class AudioChunkEvent(BaseVoiceEvent):
    type: Literal["audio_chunk"] = "audio_chunk"
    audio_base64: str
    codec: str = "pcm_s16le"
    sample_rate_hz: int = 16000
    channels: int = 1
    chunk_ms: int = 100


class CancelEvent(BaseVoiceEvent):
    type: Literal["cancel"] = "cancel"
    generation_id: str
    reason: str = "barge_in"


class VadEvent(BaseVoiceEvent):
    type: Literal["vad_event"] = "vad_event"
    event: VadEventName
