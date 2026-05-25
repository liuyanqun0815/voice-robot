"""运维可观测摘要 API（供前端仪表盘）。"""

from fastapi import APIRouter, Depends

from app.api.admin_auth import require_admin_api_key
from app.core.metrics import collect_metrics_snapshot
from app.core.settings import Settings
from app.schemas.ops import MetricSampleOut, OpsSummaryResponse

router = APIRouter(prefix="/admin/ops", tags=["admin-ops"])


@router.get("/summary", response_model=OpsSummaryResponse, dependencies=[Depends(require_admin_api_key)])
def ops_summary() -> OpsSummaryResponse:
    settings = Settings()
    missing = settings.readiness_missing_items()
    mode = "mock" if settings.mock_streaming_enabled else "live"
    ready_status = "ready" if not missing else "not_ready"
    metrics = [MetricSampleOut.model_validate(row) for row in collect_metrics_snapshot()]
    return OpsSummaryResponse(
        health_status="ok",
        ready_status=ready_status,
        mode=mode,
        audit_enabled=settings.audit_enabled,
        readiness_missing=missing,
        metrics=metrics,
    )
