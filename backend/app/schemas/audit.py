from datetime import datetime

from pydantic import BaseModel, Field


class AuditTurnOut(BaseModel):
    id: int
    trace_id: str
    session_id: str
    turn_id: str
    input_mode: str
    user_text: str
    assistant_text: str
    wiki_sources: list[str]
    retrieval_notes: list[str]
    tool_called: bool
    agent_thread_id: str
    latency_ms_e2e: int
    llm_first_token_ms: int = 0
    status: str
    error_code: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditTurnListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AuditTurnOut]


class AuditSessionOut(BaseModel):
    session_id: str
    turn_count: int
    ok_count: int
    error_count: int
    avg_latency_ms: float
    avg_first_token_ms: float
    tool_called_count: int
    first_turn_at: datetime
    last_turn_at: datetime
    last_input_mode: str = ""
    last_user_text: str = ""


class AuditSessionListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AuditSessionOut]


class AuditStatsResponse(BaseModel):
    total_turns: int
    ok_count: int
    error_count: int
    avg_latency_ms: float
    avg_first_token_ms: float = 0.0
    tool_called_count: int
    tool_called_rate: float
    wiki_hit_count: int = 0
    wiki_hit_rate: float = 0.0
    unique_sessions: int
    by_input_mode: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)
