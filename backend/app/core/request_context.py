"""WebSocket 会话上下文（ContextVar），供日志、审计、LangSmith metadata 使用。"""

from __future__ import annotations

import threading
from contextvars import ContextVar
from dataclasses import dataclass, field

from app.services.wiki_query import WikiRetrievalResult

_voice_ctx: ContextVar[VoiceContext | None] = ContextVar("voice_ctx", default=None)
_wiki_audit: ContextVar[WikiAuditSnapshot | None] = ContextVar("wiki_audit", default=None)
# 线程池跑 Agent 时的兜底存储（key=session_id:turn_id）
_wiki_audit_by_turn: dict[str, WikiAuditSnapshot] = {}
_wiki_audit_lock = threading.Lock()


@dataclass
class VoiceContext:
    trace_id: str = ""
    session_id: str = ""
    turn_id: str = ""
    input_mode: str = "voice"


@dataclass
class WikiAuditSnapshot:
    wiki_sources: list[str] = field(default_factory=list)
    retrieval_notes: list[str] = field(default_factory=list)
    tool_called: bool = False


def bind_voice_context(**kwargs: object) -> VoiceContext:
    current = _voice_ctx.get() or VoiceContext()
    data = {**current.__dict__, **{k: v for k, v in kwargs.items() if k in VoiceContext.__dataclass_fields__}}
    ctx = VoiceContext(**data)
    _voice_ctx.set(ctx)
    return ctx


def get_voice_context() -> VoiceContext | None:
    return _voice_ctx.get()


def _audit_turn_key() -> str | None:
    voice = get_voice_context()
    if voice and voice.session_id and voice.turn_id:
        return f"{voice.session_id}:{voice.turn_id}"
    return None


def reset_wiki_audit() -> None:
    empty = WikiAuditSnapshot()
    _wiki_audit.set(empty)
    key = _audit_turn_key()
    if key:
        with _wiki_audit_lock:
            _wiki_audit_by_turn[key] = WikiAuditSnapshot()


def record_wiki_retrieval(result: WikiRetrievalResult) -> None:
    key = _audit_turn_key()
    snapshot = _wiki_audit.get() or WikiAuditSnapshot()
    if key:
        with _wiki_audit_lock:
            snapshot = _wiki_audit_by_turn.get(key, WikiAuditSnapshot())
    snapshot.tool_called = True
    for path in result.collect_source_paths():
        if path not in snapshot.wiki_sources:
            snapshot.wiki_sources.append(path)
    for note in result.retrieval_notes:
        if note not in snapshot.retrieval_notes:
            snapshot.retrieval_notes.append(note)
    _wiki_audit.set(snapshot)
    if key:
        with _wiki_audit_lock:
            _wiki_audit_by_turn[key] = snapshot


def get_wiki_audit() -> WikiAuditSnapshot:
    key = _audit_turn_key()
    if key:
        with _wiki_audit_lock:
            stored = _wiki_audit_by_turn.get(key)
            if stored is not None:
                return stored
    return _wiki_audit.get() or WikiAuditSnapshot()
