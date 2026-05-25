#!/usr/bin/env python3
"""Fetch URL content (library). Use enrich_sessions.py to write inline into session files."""

from __future__ import annotations

import argparse
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

from kb_utils import (
    collect_urls_from_sessions,
    extension_from_url,
    is_image_url,
    is_meaningful_text,
    slug_from_url,
)

try:
    import trafilatura
except ImportError:
    trafilatura = None  # type: ignore

from bs4 import BeautifulSoup

try:
    from markitdown import MarkItDown
except ImportError:
    MarkItDown = None  # type: ignore

USER_AGENT = "cs-knowledge-wiki/1.0"

TEXT_EXTENSIONS = {".txt", ".log", ".json", ".xml", ".csv", ".md", ".html", ".htm", ".slurm", ".sh", ".err", ".out"}
DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".rtf", ".epub"}
SKIP_EXTENSIONS = {".mp4", ".mp3", ".zip", ".gz", ".wav"}


def extract_html(resp: requests.Response, url: str) -> str:
    raw = resp.content
    if trafilatura:
        text = trafilatura.extract(raw, url=url, include_tables=True)
        if text and len(text.strip()) > 30:
            return text
    for enc in ("utf-8", "gbk", resp.apparent_encoding or "utf-8"):
        try:
            html = raw.decode(enc)
            break
        except (UnicodeDecodeError, TypeError):
            html = raw.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    main = soup.find("article") or soup.find("main") or soup.body
    if main:
        return main.get_text("\n", strip=True)
    return soup.get_text("\n", strip=True)


def extract_with_markitdown(content: bytes, ext: str, url: str) -> str:
    if MarkItDown is None:
        return f"[需要安装 markitdown 以解析 {ext}: pip install markitdown]"
    converter = MarkItDown()
    suffix = ext if ext.startswith(".") else f".{ext}"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        result = converter.convert(tmp_path)
        text = (result.text_content or "").strip()
        if text:
            return text
        return f"[{ext} 解析结果为空，url={url}]"
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def extract_plain_text(content: bytes) -> str:
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def fetch_url(url: str, timeout: int = 60) -> tuple[str, str, str]:
    """
    Returns (url, content_type, markdown_body).
  content_kind: image | document | html | text | skip
    """
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    ext = extension_from_url(url)

    if is_image_url(url, content_type):
        return url, content_type, (
            f"> 图片附件（已跳过正文解析）\n\n"
            f"- 类型: `{content_type or 'image'}`\n"
            f"- 大小: {len(resp.content)} bytes\n"
            f"- 扩展名: `{ext or 'unknown'}`\n"
        )

    if ext in SKIP_EXTENSIONS:
        return url, content_type, (
            f"> 媒体/压缩包（已跳过正文解析）\n\n"
            f"- 类型: `{content_type}`\n"
            f"- 大小: {len(resp.content)} bytes\n"
        )

    if "text/html" in content_type or ext in {".html", ".htm"}:
        return url, content_type, extract_html(resp, url)

    if content_type.startswith("text/") or ext in TEXT_EXTENSIONS:
        text = extract_plain_text(resp.content)
        if len(text.strip()) < 500000:
            return url, content_type, text
        return url, content_type, text[:500000] + "\n\n...[truncated]"

    if ext in DOCUMENT_EXTENSIONS or any(
        t in content_type
        for t in (
            "pdf",
            "word",
            "document",
            "spreadsheet",
            "presentation",
            "msword",
            "officedocument",
        )
    ):
        use_ext = ext or ".pdf"
        return url, content_type, extract_with_markitdown(resp.content, use_ext, url)

    # 无扩展名 / 未知类型：尝试 HTML，再尝试 markitdown
    if b"<html" in resp.content[:2000].lower() or b"<!doctype" in resp.content[:2000].lower():
        try:
            resp.encoding = resp.apparent_encoding or "utf-8"
            return url, content_type, extract_html(resp, url)
        except Exception:
            pass

    if MarkItDown and len(resp.content) < 50_000_000:
        guessed = ext or ".bin"
        return url, content_type, extract_with_markitdown(resp.content, guessed, url)

    return url, content_type, f"[无法解析: {content_type}, {len(resp.content)} bytes, ext={ext or 'none'}]"


def load_urls(chats_dir: Path | None, excel: Path | None, column: str) -> list[str]:
    urls: list[str] = []
    if chats_dir and chats_dir.exists():
        urls.extend(collect_urls_from_sessions(chats_dir))
    if excel and excel.exists():
        import pandas as pd

        df = pd.read_excel(excel)
        col = column
        if col not in df.columns:
            for c in df.columns:
                if str(c).startswith("fileUrl") or "fileurl" in str(c).lower():
                    col = c
                    break
        if col not in df.columns:
            raise SystemExit(f"Column not found: {column}. Available: {list(df.columns)}")
        for val in df[col].dropna().astype(str).unique():
            val = val.strip()
            if val.startswith("http"):
                urls.append(val)
    return list(dict.fromkeys(urls))


def main() -> None:
    import sys

    print(
        "scrape_urls.py 已改为库模块，不再写入 raw/attachments/。\n"
        "请使用:\n"
        "  python enrich_sessions.py --chats-dir kefu-know/raw/chats/ --with-ocr --force\n"
        "或 process_chat_excel.py --enrich-links --with-ocr",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
