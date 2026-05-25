import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.core.request_context import bind_voice_context, record_wiki_retrieval, reset_wiki_audit
from app.core.settings import Settings
from app.services.audit_service import AuditService, get_audit_service
from app.services.wiki_query import RetrievalHit, WikiRetrievalResult


@pytest.fixture
def audit_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    db_path = tmp_path / "audit.db"
    monkeypatch.setenv("VOICE_ROBOT_AUDIT_ENABLED", "true")
    monkeypatch.setenv("VOICE_ROBOT_AUDIT_DSN", f"sqlite:///{db_path.as_posix()}")
    get_audit_service.cache_clear()
    return Settings()


def test_record_turn_persists_wiki_sources(audit_settings: Settings) -> None:
    service = AuditService(audit_settings)
    bind_voice_context(session_id="s-audit", turn_id="t-1", trace_id="tr-1", input_mode="text")
    reset_wiki_audit()
    record_wiki_retrieval(
        WikiRetrievalResult(
            question="试用期限",
            categories=[],
            faqs=[
                RetrievalHit(
                    rel_path="wiki/FAQ/试用.md",
                    title="试用",
                    excerpt="标准回答",
                    score=1.0,
                    hit_type="faq",
                    source_route="faq",
                )
            ],
            concepts=[],
            keywords=["试用"],
            retrieval_notes=["keyword_faq"],
        )
    )

    turn_id = service.record_turn(
        trace_id="tr-1",
        session_id="s-audit",
        turn_id="t-1",
        input_mode="text",
        user_text="试用多久",
        assistant_text="老师您好，试用 7 天。",
        latency_ms_e2e=120,
        status="ok",
    )

    rows = service.list_turns_by_session("s-audit")
    assert turn_id == rows[0].id
    assert rows[0].wiki_sources == ["wiki/FAQ/试用.md"]
    assert rows[0].retrieval_notes == ["keyword_faq"]
    assert rows[0].tool_called is True
    assert rows[0].assistant_text.startswith("老师")


def test_get_audit_service_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_ROBOT_AUDIT_ENABLED", "false")
    get_audit_service.cache_clear()
    assert get_audit_service() is None
