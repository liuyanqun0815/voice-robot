from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app.core.logging_setup import configure_logging
from app.core.metrics import metrics_content_type, metrics_response_body
from app.core.settings import Settings
from app.services.audit_service import get_audit_service

configure_logging()

settings = Settings()
settings.apply_langsmith_environment()
settings.validate_live_credentials()

from app.api.admin_audit import router as admin_audit_router  # noqa: E402
from app.api.admin_ops import router as admin_ops_router  # noqa: E402
from app.ws.voice_endpoint import router as voice_router  # noqa: E402

app = FastAPI(title="Voice Robot API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(voice_router)
app.include_router(admin_audit_router)
app.include_router(admin_ops_router)


@app.on_event("startup")
def startup_audit_db() -> None:
    if settings.audit_enabled:
        get_audit_service()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz", response_model=None)
def readyz() -> JSONResponse | dict[str, object]:
    missing = settings.readiness_missing_items()
    mode = "mock" if settings.mock_streaming_enabled else "live"
    body: dict[str, object] = {"status": "ready", "mode": mode, "audit_enabled": settings.audit_enabled}
    if missing:
        body = {"status": "not_ready", "mode": "live", "missing": missing}
        return JSONResponse(body, status_code=503)
    return body


@app.get("/metrics")
def metrics() -> Response:
    return Response(content=metrics_response_body(), media_type=metrics_content_type())
