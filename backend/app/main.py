from fastapi import FastAPI

from app.core.logging_setup import configure_logging
from app.core.settings import Settings
from app.ws.voice_endpoint import router as voice_router

configure_logging()

settings = Settings()
settings.validate_live_credentials()
app = FastAPI(title="Voice Robot API", version="0.1.0")
app.include_router(voice_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
