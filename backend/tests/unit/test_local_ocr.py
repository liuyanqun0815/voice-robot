import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.core.settings import Settings
from app.services.local_ocr import (
    format_local_ocr_for_agent,
    is_meaningful_ocr_text,
    ocr_image_bytes,
    ocr_image_url_local,
)


def test_is_meaningful_ocr_text() -> None:
    assert is_meaningful_ocr_text("这是一段足够长的 OCR 识别结果文字内容") is True
    assert is_meaningful_ocr_text("短") is False


def test_ocr_image_bytes_parses_engine_output() -> None:
    engine = MagicMock()
    engine.return_value = ([[None, "连接超时"], [None, "错误码 E001"]], None)
    text = ocr_image_bytes(engine, b"fake")
    assert "连接超时" in text
    assert "E001" in text


def test_format_ocr_text_for_agent() -> None:
    long_text = "这是一段足够长的 OCR 识别结果文字内容用于测试"
    formatted = format_local_ocr_for_agent(long_text)
    assert long_text in formatted
    assert "标注" in formatted


def test_ocr_image_url_local() -> None:
    async def _run() -> str:
        with (
            patch("app.services.local_ocr.download_image", new_callable=AsyncMock) as mock_download,
            patch("app.services.local_ocr.run_local_ocr_on_bytes") as mock_ocr,
        ):
            mock_download.return_value = b"img-bytes"
            mock_ocr.return_value = "这是一段足够长的 OCR 识别结果文字内容"
            return await ocr_image_url_local("https://example.com/a.png", settings=Settings())

    result = asyncio.run(_run())
    assert "足够长" in result
