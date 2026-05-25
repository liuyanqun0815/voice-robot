#!/usr/bin/env python3
"""OCR helpers (library). Inline output only — use enrich_sessions.py."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import requests

from kb_utils import extension_from_url, is_image_url, is_meaningful_text

USER_AGENT = "cs-knowledge-wiki/1.0"


def get_ocr_engine():
    try:
        from rapidocr_onnxruntime import RapidOCR

        return RapidOCR()
    except ImportError as exc:
        raise SystemExit("请安装: pip install rapidocr-onnxruntime") from exc


def download_image(url: str, timeout: int = 30) -> bytes:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def ocr_image_bytes(engine, image_bytes: bytes) -> str:
    result, _ = engine(image_bytes)
    if not result:
        return ""
    lines = []
    for item in result:
        if len(item) >= 2 and item[1]:
            text = str(item[1]).strip()
            if text:
                lines.append(text)
    return "\n".join(lines)


def ocr_url(url: str, engine=None) -> str | None:
    """OCR 单张图片 URL，无意义则返回 None。"""
    if engine is None:
        engine = get_ocr_engine()
    text = ocr_image_bytes(engine, download_image(url))
    return text if is_meaningful_text(text, min_chars=15) else None


def main() -> None:
    print(
        "ocr_images.py 已改为库模块，不再写入 raw/attachments/。\n"
        "请使用:\n"
        "  python enrich_sessions.py --chats-dir kefu-know/raw/chats/ --with-ocr\n"
        "或 process_chat_excel.py --enrich-links --with-ocr",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
