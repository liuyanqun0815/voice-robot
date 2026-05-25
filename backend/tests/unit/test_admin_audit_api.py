import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.core.request_context import bind_voice_context, record_wiki_retrieval, reset_wiki_audit
from app.core.settings import Settings
from app.main import app
from app.services.audit_service import AuditService, get_audit_service
from app.services.wiki_query import RetrievalHit, WikiRetrievalResult


@pytest.fixture
def audit_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "audit_api.db"
    monkeypatch.setenv("VOICE_ROBOT_AUDIT_ENABLED", "true")
    monkeypatch.setenv("VOICE_ROBOT_AUDIT_DSN", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("VOICE_ROBOT_AUDIT_ADMIN_API_KEY", "test-admin-key")
    get_audit_service.cache_clear()

    service = AuditService(Settings())
    bind_voice_context(session_id="s-api", turn_id="t-1", trace_id="tr-api", input_mode="text")
    reset_wiki_audit()
    record_wiki_retrieval(
        WikiRetrievalResult(
            question="q",
            categories=[],
            faqs=[
                RetrievalHit(
                    rel_path="wiki/FAQ/a.md",
                    title="A",
                    excerpt="ans",
                    score=1.0,
                    hit_type="faq",
                )
            ],
            concepts=[],
            keywords=[],
            retrieval_notes=["keyword_faq"],
        )
    )
    service.record_turn(
        trace_id="tr-api",
        session_id="s-api",
        turn_id="t-1",
        input_mode="text",
        user_text="问题一",
        assistant_text="回答一",
        latency_ms_e2e=100,
        status="ok",
    )
    reset_wiki_audit()
    service.record_turn(
        trace_id="tr-api-2",
        session_id="s-api",
        turn_id="t-2",
        input_mode="voice",
        user_text="问题二",
        assistant_text="",
        latency_ms_e2e=200,
        status="error",
        error_code="ORCHESTRATOR_FAILED",
    )
    return TestClient(app)


def test_dashboard_redirects_to_frontend(audit_client: TestClient) -> None:
    response = audit_client.get("/admin/audit/dashboard", follow_redirects=False)
    assert response.status_code == 302
    assert "5173" in response.headers["location"]

    legacy = audit_client.get("/admin/audit/dashboard?legacy=1")
    assert legacy.status_code == 200
    assert "审计仪表盘" in legacy.text


def test_list_sessions_requires_api_key(audit_client: TestClient) -> None:
    denied = audit_client.get("/admin/audit/sessions")
    assert denied.status_code == 401

    ok = audit_client.get("/admin/audit/sessions", headers={"X-Admin-Api-Key": "test-admin-key"})
    assert ok.status_code == 200
    payload = ok.json()
    assert payload["total"] == 1
    assert payload["items"][0]["session_id"] == "s-api"
    assert payload["items"][0]["turn_count"] == 2


def test_session_turns_list(audit_client: TestClient) -> None:
    headers = {"X-Admin-Api-Key": "test-admin-key"}
    response = audit_client.get("/admin/audit/sessions/s-api/turns", headers=headers)
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2
    assert items[0]["turn_id"] == "t-1"
    assert items[0]["llm_first_token_ms"] == 0


def test_list_turns_requires_api_key(audit_client: TestClient) -> None:
    denied = audit_client.get("/admin/audit/turns")
    assert denied.status_code == 401

    ok = audit_client.get("/admin/audit/turns", headers={"X-Admin-Api-Key": "test-admin-key"})
    assert ok.status_code == 200
    payload = ok.json()
    assert payload["total"] == 2
    assert payload["items"][0]["session_id"] == "s-api"


def test_stats_and_filter(audit_client: TestClient) -> None:
    headers = {"X-Admin-Api-Key": "test-admin-key"}
    stats = audit_client.get("/admin/audit/stats", headers=headers)
    assert stats.status_code == 200
    body = stats.json()
    assert body["total_turns"] == 2
    assert body["error_count"] == 1
    assert body["tool_called_count"] == 1

    filtered = audit_client.get(
        "/admin/audit/turns",
        params={"session_id": "s-api", "status": "ok"},
        headers=headers,
    )
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["status"] == "ok"


def test_get_turn_by_id(audit_client: TestClient) -> None:
    headers = {"X-Admin-Api-Key": "test-admin-key"}
    listed = audit_client.get("/admin/audit/turns", headers=headers).json()
    ok_turn = next(item for item in listed["items"] if item["turn_id"] == "t-1")
    detail = audit_client.get(f"/admin/audit/turns/{ok_turn['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["wiki_sources"] == ["wiki/FAQ/a.md"]


def test_export_csv(audit_client: TestClient) -> None:
    response = audit_client.get(
        "/admin/audit/export.csv",
        headers={"X-Admin-Api-Key": "test-admin-key"},
    )
    assert response.status_code == 200
    assert "text/csv" in response.headers.get("content-type", "")
    assert "session_id" in response.text
    assert "s-api" in response.text
