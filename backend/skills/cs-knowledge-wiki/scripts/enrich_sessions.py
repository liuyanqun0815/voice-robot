#!/usr/bin/env python3
"""Fetch link/image content and write directly under each session (no attachments dir)."""

from __future__ import annotations

import argparse
from pathlib import Path

from kb_utils import collect_all_urls_from_session_text, is_image_url, is_meaningful_text

SECTION_TITLE = "## 链接与附件内容"


def fetch_link_body(url: str, ocr_engine=None) -> str | None:
    """抓取网页正文或图片 OCR，无意义则返回 None。"""
    try:
        if is_image_url(url):
            if ocr_engine is None:
                return None
            from ocr_images import ocr_url

            return ocr_url(url, ocr_engine)

        from scrape_urls import fetch_url

        _, _content_type, text = fetch_url(url)
        return text if is_meaningful_text(text, min_chars=25) else None
    except Exception:
        return None


def build_section(urls: list[str], ocr_engine=None) -> str:
    blocks = []
    for url in urls:
        body = fetch_link_body(url, ocr_engine)
        if not body:
            continue
        name = url.split("/")[-1].split("?")[0] or url
        kind = "图片 OCR" if is_image_url(url) else "网页"
        blocks.append(f"### [{kind}] {name}\n\n来源: {url}\n\n{body.strip()}\n")

    if not blocks:
        return ""
    return SECTION_TITLE + "\n\n" + "\n".join(blocks) + "\n"


def enrich_session(path: Path, ocr_engine=None, force: bool = False) -> bool:
    text = path.read_text(encoding="utf-8")
    urls = collect_all_urls_from_session_text(text)
    if not urls:
        if SECTION_TITLE in text and force:
            idx = text.index(SECTION_TITLE)
            path.write_text(text[:idx].rstrip() + "\n", encoding="utf-8")
        return False

    if SECTION_TITLE in text and not force:
        return False

    section = build_section(urls, ocr_engine)
    if not section:
        if SECTION_TITLE in text:
            idx = text.index(SECTION_TITLE)
            path.write_text(text[:idx].rstrip() + "\n", encoding="utf-8")
        return False

    if SECTION_TITLE in text:
        idx = text.index(SECTION_TITLE)
        text = text[:idx].rstrip() + "\n\n" + section
    else:
        text = text.rstrip() + "\n\n" + section

    path.write_text(text, encoding="utf-8")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Inline link/OCR content into session markdown.")
    parser.add_argument("--chats-dir", type=Path, required=True)
    parser.add_argument("--with-ocr", action="store_true", help="OCR image URLs (png/jpg etc.)")
    parser.add_argument("--force", action="store_true", help="Re-fetch even if section exists")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    ocr_engine = None
    if args.with_ocr:
        from ocr_images import get_ocr_engine

        ocr_engine = get_ocr_engine()

    sessions = sorted(args.chats_dir.glob("session_*.md"))
    if args.limit > 0:
        sessions = sessions[: args.limit]

    updated = 0
    for path in sessions:
        if enrich_session(path, ocr_engine, args.force):
            updated += 1

    print(f"Done. enriched={updated} / {len(sessions)} sessions (inline, no attachments dir)")


if __name__ == "__main__":
    main()
