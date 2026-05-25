"""对话轮次审计落库（SQLite / 任意 SQLAlchemy DSN）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

from sqlalchemy import case, create_engine, func, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.core.request_context import get_wiki_audit
from app.core.settings import Settings
from app.db.audit_models import AuditBase, ConversationTurn

logger = logging.getLogger(__name__)


@dataclass
class AuditQueryFilters:
    session_id: str | None = None
    turn_id: str | None = None
    trace_id: str | None = None
    status: str | None = None
    input_mode: str | None = None
    from_ts: datetime | None = None
    to_ts: datetime | None = None


class AuditService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        dsn = settings.resolve_audit_dsn()
        self._engine = create_engine(dsn, future=True)
        AuditBase.metadata.create_all(self._engine)
        self._migrate_schema()
        self._session_factory = sessionmaker(bind=self._engine, autoflush=False, autocommit=False, future=True)
        logger.info("audit db initialized: %s", dsn)

    def _migrate_schema(self) -> None:
        inspector = inspect(self._engine)
        if not inspector.has_table("conversation_turn"):
            return
        columns = {column["name"] for column in inspector.get_columns("conversation_turn")}
        if "llm_first_token_ms" not in columns:
            with self._engine.begin() as conn:
                conn.execute(
                    text("ALTER TABLE conversation_turn ADD COLUMN llm_first_token_ms INTEGER NOT NULL DEFAULT 0")
                )
            logger.info("audit db migrated: added llm_first_token_ms column")

    def _truncate_user_text(self, text: str) -> str:
        if not self._settings.audit_store_user_text:
            return ""
        limit = self._settings.audit_max_user_text_chars
        if limit > 0 and len(text) > limit:
            return text[:limit]
        return text

    def record_turn(
        self,
        *,
        trace_id: str,
        session_id: str,
        turn_id: str,
        input_mode: str,
        user_text: str,
        assistant_text: str,
        latency_ms_e2e: int,
        llm_first_token_ms: int = 0,
        status: str = "ok",
        error_code: str | None = None,
        wiki_sources: list[str] | None = None,
        retrieval_notes: list[str] | None = None,
        tool_called: bool | None = None,
    ) -> int:
        wiki_snapshot = get_wiki_audit()
        row = ConversationTurn(
            trace_id=trace_id,
            session_id=session_id,
            turn_id=turn_id,
            input_mode=input_mode,
            user_text=self._truncate_user_text(user_text),
            assistant_text=assistant_text,
            wiki_sources=wiki_sources if wiki_sources is not None else list(wiki_snapshot.wiki_sources),
            retrieval_notes=retrieval_notes if retrieval_notes is not None else list(wiki_snapshot.retrieval_notes),
            tool_called=tool_called if tool_called is not None else wiki_snapshot.tool_called,
            agent_thread_id=session_id,
            latency_ms_e2e=latency_ms_e2e,
            llm_first_token_ms=llm_first_token_ms,
            status=status,
            error_code=error_code,
        )
        with self._session_factory() as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            turn_pk = row.id
        logger.info(
            "audit turn saved id=%s session_id=%s turn_id=%s status=%s e2e_ms=%s ttft_ms=%s tool_called=%s sources=%s",
            turn_pk,
            session_id,
            turn_id,
            status,
            latency_ms_e2e,
            llm_first_token_ms,
            row.tool_called,
            len(row.wiki_sources),
        )
        return turn_pk

    def list_turns_by_session(self, session_id: str, *, limit: int = 50) -> list[ConversationTurn]:
        items, _ = self.query_turns(AuditQueryFilters(session_id=session_id), limit=limit, offset=0)
        return items

    def _apply_filters(self, stmt, filters: AuditQueryFilters):
        if filters.session_id:
            stmt = stmt.where(ConversationTurn.session_id == filters.session_id)
        if filters.turn_id:
            stmt = stmt.where(ConversationTurn.turn_id == filters.turn_id)
        if filters.trace_id:
            stmt = stmt.where(ConversationTurn.trace_id == filters.trace_id)
        if filters.status:
            stmt = stmt.where(ConversationTurn.status == filters.status)
        if filters.input_mode:
            stmt = stmt.where(ConversationTurn.input_mode == filters.input_mode)
        if filters.from_ts is not None:
            stmt = stmt.where(ConversationTurn.created_at >= filters.from_ts)
        if filters.to_ts is not None:
            stmt = stmt.where(ConversationTurn.created_at <= filters.to_ts)
        return stmt

    def query_turns(
        self,
        filters: AuditQueryFilters,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ConversationTurn], int]:
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        base = select(ConversationTurn)
        base = self._apply_filters(base, filters)
        count_stmt = select(func.count()).select_from(base.subquery())
        list_stmt = base.order_by(ConversationTurn.id.desc()).offset(offset).limit(limit)
        with self._session_factory() as session:
            total = int(session.scalar(count_stmt) or 0)
            items = list(session.scalars(list_stmt).all())
        return items, total

    def query_sessions(
        self,
        filters: AuditQueryFilters,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, object]], int]:
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        base = select(ConversationTurn)
        base = self._apply_filters(base, filters)
        subq = base.subquery()

        grouped = (
            select(
                subq.c.session_id.label("session_id"),
                func.count(subq.c.id).label("turn_count"),
                func.sum(case((subq.c.status == "ok", 1), else_=0)).label("ok_count"),
                func.sum(case((subq.c.status != "ok", 1), else_=0)).label("error_count"),
                func.avg(subq.c.latency_ms_e2e).label("avg_latency_ms"),
                func.avg(subq.c.llm_first_token_ms).label("avg_first_token_ms"),
                func.sum(case((subq.c.tool_called.is_(True), 1), else_=0)).label("tool_called_count"),
                func.min(subq.c.created_at).label("first_turn_at"),
                func.max(subq.c.created_at).label("last_turn_at"),
                func.max(subq.c.id).label("last_turn_id"),
            )
            .group_by(subq.c.session_id)
            .subquery()
        )

        count_stmt = select(func.count()).select_from(grouped)
        list_stmt = select(grouped).order_by(grouped.c.last_turn_at.desc()).offset(offset).limit(limit)

        with self._session_factory() as session:
            total = int(session.scalar(count_stmt) or 0)
            rows = session.execute(list_stmt).all()

        items: list[dict[str, object]] = []
        with self._session_factory() as session:
            for row in rows:
                last_turn = session.get(ConversationTurn, int(row.last_turn_id))
                items.append(
                    {
                        "session_id": row.session_id,
                        "turn_count": int(row.turn_count or 0),
                        "ok_count": int(row.ok_count or 0),
                        "error_count": int(row.error_count or 0),
                        "avg_latency_ms": round(float(row.avg_latency_ms or 0.0), 1),
                        "avg_first_token_ms": round(float(row.avg_first_token_ms or 0.0), 1),
                        "tool_called_count": int(row.tool_called_count or 0),
                        "first_turn_at": row.first_turn_at,
                        "last_turn_at": row.last_turn_at,
                        "last_input_mode": last_turn.input_mode if last_turn else "",
                        "last_user_text": last_turn.user_text if last_turn else "",
                    }
                )
        return items, total

    def get_turn(self, turn_pk: int) -> ConversationTurn | None:
        stmt = select(ConversationTurn).where(ConversationTurn.id == turn_pk)
        with self._session_factory() as session:
            return session.scalar(stmt)

    def get_stats(self, filters: AuditQueryFilters) -> dict[str, object]:
        base = select(ConversationTurn)
        base = self._apply_filters(base, filters)
        subq = base.subquery()
        agg_stmt = select(
            func.count(subq.c.id),
            func.sum(case((subq.c.status == "ok", 1), else_=0)),
            func.sum(case((subq.c.status != "ok", 1), else_=0)),
            func.avg(subq.c.latency_ms_e2e),
            func.avg(subq.c.llm_first_token_ms),
            func.sum(case((subq.c.tool_called.is_(True), 1), else_=0)),
            func.count(func.distinct(subq.c.session_id)),
        )
        with self._session_factory() as session:
            row = session.execute(agg_stmt).one()
        total = int(row[0] or 0)
        ok_count = int(row[1] or 0)
        error_count = int(row[2] or 0)
        avg_latency = float(row[3] or 0.0)
        avg_first_token = float(row[4] or 0.0)
        tool_called_count = int(row[5] or 0)
        unique_sessions = int(row[6] or 0)

        by_input_mode: dict[str, int] = {}
        by_status: dict[str, int] = {}
        mode_stmt = select(subq.c.input_mode, func.count()).group_by(subq.c.input_mode)
        status_stmt = select(subq.c.status, func.count()).group_by(subq.c.status)
        wiki_hit_count = 0
        with self._session_factory() as session:
            for mode, count in session.execute(mode_stmt).all():
                by_input_mode[str(mode)] = int(count)
            for status, count in session.execute(status_stmt).all():
                by_status[str(status)] = int(count)
            for sources, called in session.execute(select(subq.c.wiki_sources, subq.c.tool_called)).all():
                if called and isinstance(sources, list) and len(sources) > 0:
                    wiki_hit_count += 1

        tool_called_rate = (tool_called_count / total) if total else 0.0
        wiki_hit_rate = (wiki_hit_count / total) if total else 0.0
        return {
            "total_turns": total,
            "ok_count": ok_count,
            "error_count": error_count,
            "avg_latency_ms": round(avg_latency, 1),
            "avg_first_token_ms": round(avg_first_token, 1),
            "tool_called_count": tool_called_count,
            "tool_called_rate": round(tool_called_rate, 4),
            "wiki_hit_count": wiki_hit_count,
            "wiki_hit_rate": round(wiki_hit_rate, 4),

            "unique_sessions": unique_sessions,
            "by_input_mode": by_input_mode,
            "by_status": by_status,
        }

    def iter_turns_for_export(self, filters: AuditQueryFilters, *, limit: int = 5000) -> list[ConversationTurn]:
        items, _ = self.query_turns(filters, limit=limit, offset=0)
        return items


@lru_cache
def get_audit_service() -> AuditService | None:
    settings = Settings()
    if not settings.audit_enabled:
        return None
    return AuditService(settings)
