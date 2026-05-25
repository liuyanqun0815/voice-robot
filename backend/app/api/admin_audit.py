"""审计查询 API 与仪表盘（Phase 2）。"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

from app.api.admin_auth import require_admin_api_key
from app.core.settings import Settings
from app.schemas.audit import (
    AuditSessionListResponse,
    AuditSessionOut,
    AuditStatsResponse,
    AuditTurnListResponse,
    AuditTurnOut,
)
from app.services.audit_service import AuditQueryFilters, AuditService, get_audit_service

router = APIRouter(prefix="/admin/audit", tags=["admin-audit"])

_DASHBOARD_PATH = Path(__file__).resolve().parent.parent / "static" / "audit_dashboard.html"


def _get_service() -> AuditService:
    service = get_audit_service()
    if service is None:
        raise HTTPException(status_code=503, detail="Audit is disabled")
    return service


def _parse_datetime(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    if not value or not value.strip():
        return None
    raw = value.strip()
    try:
        if len(raw) == 10:
            dt = datetime.strptime(raw, "%Y-%m-%d")
            if end_of_day:
                dt = dt.replace(hour=23, minute=59, second=59)
            return dt.replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid datetime: {value}") from exc


def _build_filters(
    session_id: str | None = None,
    turn_id: str | None = None,
    trace_id: str | None = None,
    status: str | None = None,
    input_mode: str | None = None,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
) -> AuditQueryFilters:
    return AuditQueryFilters(
        session_id=session_id or None,
        turn_id=turn_id or None,
        trace_id=trace_id or None,
        status=status or None,
        input_mode=input_mode or None,
        from_ts=_parse_datetime(from_),
        to_ts=_parse_datetime(to, end_of_day=True),
    )


@router.get("/dashboard")
def audit_dashboard(legacy: bool = Query(default=False, description="legacy=1 打开旧版静态页")):
    if legacy and _DASHBOARD_PATH.is_file():
        return FileResponse(_DASHBOARD_PATH, media_type="text/html; charset=utf-8")
    return RedirectResponse(url=Settings().frontend_ops_url, status_code=302)


@router.get("/sessions", response_model=AuditSessionListResponse, dependencies=[Depends(require_admin_api_key)])
def list_audit_sessions(
    session_id: str | None = None,
    status: str | None = None,
    input_mode: str | None = None,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AuditSessionListResponse:
    service = _get_service()
    filters = _build_filters(session_id=session_id, status=status, input_mode=input_mode, from_=from_, to=to)
    items, total = service.query_sessions(filters, limit=limit, offset=offset)
    return AuditSessionListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[AuditSessionOut.model_validate(row) for row in items],
    )


@router.get(
    "/sessions/{session_id}/turns",
    response_model=AuditTurnListResponse,
    dependencies=[Depends(require_admin_api_key)],
)
def list_session_turns(session_id: str, limit: int = Query(default=100, ge=1, le=200)) -> AuditTurnListResponse:
    service = _get_service()
    items, total = service.query_turns(AuditQueryFilters(session_id=session_id), limit=limit, offset=0)
    items = sorted(items, key=lambda row: row.id)
    return AuditTurnListResponse(
        total=total,
        limit=limit,
        offset=0,
        items=[AuditTurnOut.model_validate(row) for row in items],
    )


@router.get("/turns", response_model=AuditTurnListResponse, dependencies=[Depends(require_admin_api_key)])
def list_audit_turns(
    session_id: str | None = None,
    turn_id: str | None = None,
    trace_id: str | None = None,
    status: str | None = None,
    input_mode: str | None = None,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AuditTurnListResponse:
    service = _get_service()
    filters = _build_filters(
        session_id=session_id,
        turn_id=turn_id,
        trace_id=trace_id,
        status=status,
        input_mode=input_mode,
        from_=from_,
        to=to,
    )
    items, total = service.query_turns(filters, limit=limit, offset=offset)
    return AuditTurnListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[AuditTurnOut.model_validate(row) for row in items],
    )


@router.get("/turns/{turn_pk}", response_model=AuditTurnOut, dependencies=[Depends(require_admin_api_key)])
def get_audit_turn(turn_pk: int) -> AuditTurnOut:
    service = _get_service()
    row = service.get_turn(turn_pk)
    if row is None:
        raise HTTPException(status_code=404, detail="Turn not found")
    return AuditTurnOut.model_validate(row)


@router.get("/stats", response_model=AuditStatsResponse, dependencies=[Depends(require_admin_api_key)])
def audit_stats(
    session_id: str | None = None,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
) -> AuditStatsResponse:
    service = _get_service()
    filters = _build_filters(session_id=session_id, from_=from_, to=to)
    payload = service.get_stats(filters)
    return AuditStatsResponse.model_validate(payload)


@router.get("/export.csv", dependencies=[Depends(require_admin_api_key)])
def export_audit_csv(
    session_id: str | None = None,
    status: str | None = None,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    limit: int = Query(default=2000, ge=1, le=5000),
) -> StreamingResponse:
    service = _get_service()
    filters = _build_filters(session_id=session_id, status=status, from_=from_, to=to)
    rows = service.iter_turns_for_export(filters, limit=limit)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "id",
            "created_at",
            "session_id",
            "turn_id",
            "trace_id",
            "input_mode",
            "status",
            "latency_ms_e2e",
            "llm_first_token_ms",
            "tool_called",
            "wiki_sources",
            "retrieval_notes",
            "user_text",
            "assistant_text",
            "error_code",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row.id,
                row.created_at.isoformat() if row.created_at else "",
                row.session_id,
                row.turn_id,
                row.trace_id,
                row.input_mode,
                row.status,
                row.latency_ms_e2e,
                row.llm_first_token_ms,
                row.tool_called,
                "|".join(row.wiki_sources or []),
                "|".join(row.retrieval_notes or []),
                row.user_text,
                row.assistant_text,
                row.error_code or "",
            ]
        )

    buffer.seek(0)
    filename = f"audit_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
