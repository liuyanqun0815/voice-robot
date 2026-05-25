import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.core.settings import Settings
from app.services.user_content_enricher import ocr_image_url


def test_ocr_image_url_falls_back_to_local_on_vision_error() -> None:
    settings = Settings(
        mock_streaming_enabled=False,
        deepagent_ark_api_key="test-key",
        link_enrichment_local_ocr_fallback_enabled=True,
    )

    async def _run() -> str:
        with (
            patch(
                "app.services.user_content_enricher._ocr_image_url_vision",
                new_callable=AsyncMock,
                side_effect=RuntimeError("vision model error"),
            ),
            patch(
                "app.services.user_content_enricher.ocr_image_url_local",
                new_callable=AsyncMock,
                return_value="本地识别：连接超时错误",
            ),
        ):
            return await ocr_image_url("https://example.com/shot.png", settings=settings)

    result = asyncio.run(_run())
    assert "本地 RapidOCR 兜底" in result
    assert "连接超时" in result
