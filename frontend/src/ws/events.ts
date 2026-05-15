export type BaseEvent = {
  type: string;
  session_id: string;
  turn_id: string;
  seq?: number;
  trace_id?: string;
  timestamp_ms: number;
};

export type TurnCommitRequestEvent = BaseEvent & {
  type: "turn_commit_request";
  reason: string;
};

export type AudioChunkEvent = BaseEvent & {
  type: "audio_chunk";
  audio_base64: string;
  codec?: string;
  sample_rate_hz?: number;
  channels?: number;
  chunk_ms?: number;
};

export type CancelEvent = BaseEvent & {
  type: "cancel";
  generation_id: string;
  reason: string;
};

export type VadEvent = BaseEvent & {
  type: "vad_event";
  event: "speech_start" | "speech_end";
};

export type AsrPartialEvent = BaseEvent & {
  type: "asr_partial";
  text: string;
};

export type LlmChunkEvent = BaseEvent & {
  type: "llm_chunk";
  text: string;
};

/** 助手回复文本增量（与 DeepAgent 流式输出对齐，按字符/子串下发） */
export type LlmDeltaEvent = BaseEvent & {
  type: "llm_delta";
  text: string;
};

export type TurnCommittedEvent = BaseEvent & {
  type: "turn_committed";
  generation_id: string;
};

export type TtsChunkEvent = BaseEvent & {
  type: "tts_chunk";
  chunk_index: number;
  audio_base64: string;
};
