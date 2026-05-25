from fastapi import Header, HTTPException

from app.core.settings import Settings


def require_admin_api_key(x_admin_api_key: str | None = Header(default=None, alias="X-Admin-Api-Key")) -> None:
    settings = Settings()
    if not settings.audit_enabled:
        raise HTTPException(status_code=503, detail="Audit is disabled (VOICE_ROBOT_AUDIT_ENABLED=false)")
    expected = settings.audit_admin_api_key.get_secret_value()
    if not expected:
        raise HTTPException(status_code=503, detail="Admin API key not configured (VOICE_ROBOT_AUDIT_ADMIN_API_KEY)")
    if not x_admin_api_key or x_admin_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Api-Key")
