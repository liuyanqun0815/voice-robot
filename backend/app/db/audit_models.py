from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class AuditBase(DeclarativeBase):
    pass


class ConversationTurn(AuditBase):
    __tablename__ = "conversation_turn"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(128), index=True, default="")
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    turn_id: Mapped[str] = mapped_column(String(64), index=True)
    input_mode: Mapped[str] = mapped_column(String(16), default="voice")
    user_text: Mapped[str] = mapped_column(Text, default="")
    assistant_text: Mapped[str] = mapped_column(Text, default="")
    wiki_sources: Mapped[list] = mapped_column(JSON, default=list)
    retrieval_notes: Mapped[list] = mapped_column(JSON, default=list)
    tool_called: Mapped[bool] = mapped_column(default=False)
    agent_thread_id: Mapped[str] = mapped_column(String(64), default="")
    latency_ms_e2e: Mapped[int] = mapped_column(Integer, default=0)
    llm_first_token_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="ok")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
