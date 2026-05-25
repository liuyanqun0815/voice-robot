"""本地 OCR（RapidOCR），对齐 cs-knowledge-wiki/scripts/ocr_images.py。"""

from __future__ import annotations

import logging
import re
from functools import lru_cache

import httpx

from app.core.settings import Settings

logger = logging.getLogger(__name__)

_OCR_LOW_VALUE_PATTERNS = (
    re.compile(r"^[_\-\s\d\.\:：]+$"),
    re.compile(r"^第\s*\d+\s*页"),
    re.compile(r"^截图"),
    re.compile(r"^图片$"),
)


def is_meaningful_ocr_text(text: str, *, min_chars: int = 15) -> bool:
    if not text or not text.strip():
        return False
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(pattern.match(stripped) for pattern in _OCR_LOW_VALUE_PATTERNS):
            continue
        lines.append(stripped)
    body = "\n".join(lines).strip()
    return len(body) >= min_chars


@lru_cache
def get_ocr_engine():
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise ImportError("请安装本地 OCR 依赖: pip install rapidocr-onnxruntime") from exc
    return RapidOCR()


def ocr_image_bytes(engine, image_bytes: bytes) -> str:
    """与 ocr_images.ocr_image_bytes 一致。"""
    result, _ = engine(image_bytes)
    if not result:
        return ""
    lines: list[str] = []
    for item in result:
        if len(item) >= 2 and item[1]:
            text = str(item[1]).strip()
            if text:
                lines.append(text)
    return "\n".join(lines)


async def download_image(url: str, *, settings: Settings) -> bytes:
    headers = {"User-Agent": settings.link_enrichment_user_agent}
    timeout = settings.link_enrichment_timeout_seconds
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.content


def run_local_ocr_on_bytes(image_bytes: bytes) -> str:
    engine = get_ocr_engine()
    return ocr_image_bytes(engine, image_bytes)


def format_local_ocr_for_agent(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return "（本地 OCR 未识别到有效文字；截图中的箭头/框选标注需结合客户文字说明理解）"
    if not is_meaningful_ocr_text(cleaned):
        return (
            f"（本地 OCR 仅识别到少量文字）\n{cleaned}\n\n"
            "说明：本地 OCR 无法解析标注含义；若图中有圈选/箭头，请以客户文字为准。"
        )
    return (
        f"{cleaned}\n\n"
        "说明：以上为本地 OCR 提取的文字；若图中有客户标注（箭头、框选等），请结合客户描述理解。"
    )


async def ocr_image_url_local(url: str, *, settings: Settings) -> str:
    image_bytes = await download_image(url, settings=settings)
    import asyncio

    raw_text = await asyncio.to_thread(run_local_ocr_on_bytes, image_bytes)
    logger.info("local ocr done url=%s chars=%s", url[:80], len(raw_text))
    return format_local_ocr_for_agent(raw_text)
