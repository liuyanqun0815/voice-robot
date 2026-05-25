import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.main import app
from app.services.audit_service import get_audit_service


@pytest.fixture
def ops_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("VOICE_ROBOT_AUDIT_ENABLED", "true")
    monkeypatch.setenv("VOICE_ROBOT_AUDIT_ADMIN_API_KEY", "test-admin-key")
    get_audit_service.cache_clear()
    return TestClient(app)


def test_ops_summary_requires_api_key(ops_client: TestClient) -> None:
    denied = ops_client.get("/admin/ops/summary")
    assert denied.status_code == 401


def test_ops_summary_returns_metrics(ops_client: TestClient) -> None:
    response = ops_client.get("/admin/ops/summary", headers={"X-Admin-Api-Key": "test-admin-key"})
    assert response.status_code == 200
    body = response.json()
    assert body["ready_status"] in ("ready", "not_ready")
    assert isinstance(body["metrics"], list)
