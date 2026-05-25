import asyncio
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.core.settings import Settings
from app.services.user_content_enricher import (
    extract_http_urls,
    format_enriched_message,
    guess_url_kind,
    html_to_text,
    is_safe_http_url,
)
from app.services.user_content_enricher import AttachmentEnrichment, enrich_user_text_for_agent


def test_extract_http_urls() -> None:
    text = "请看 https://example.com/a.png 和 https://foo.com/page?id=1 ，谢谢"
    urls = extract_http_urls(text)
    assert urls == ["https://example.com/a.png", "https://foo.com/page?id=1"]


def test_guess_url_kind() -> None:
    assert guess_url_kind("https://cdn.example.com/x.jpeg") == "image"
    assert guess_url_kind("https://example.com/docs/guide") == "page"


def test_is_safe_http_url_blocks_localhost() -> None:
    assert is_safe_http_url("https://example.com/x") is True
    assert is_safe_http_url("http://127.0.0.1/secret") is False


def test_html_to_text_strips_script() -> None:
    html = "<html><script>bad()</script><body><h1>标题</h1><p>正文</p></body></html>"
    assert "标题" in html_to_text(html)
    assert "bad()" not in html_to_text(html)


def test_format_enriched_message() -> None:
    body = format_enriched_message(
        "帮我看看截图",
        [AttachmentEnrichment(url="https://x.com/a.png", kind="image", content="标注了报错区域")],
    )
    assert "【客户原始消息】" in body
    assert "图片 OCR" in body
    assert "标注了报错区域" in body


def test_enrich_disabled_returns_original() -> None:
    settings = Settings(link_enrichment_enabled=False)
    text = "链接 https://example.com/page"
    assert asyncio.run(enrich_user_text_for_agent(text, settings)) == text


def test_enrich_composes_message(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_enrich(url: str, *, settings: Settings) -> AttachmentEnrichment:
        return AttachmentEnrichment(url=url, kind="image", content="客户用红框标注了「连接超时」")

    monkeypatch.setattr("app.services.user_content_enricher.enrich_attachment", fake_enrich)
    settings = Settings(link_enrichment_enabled=True, mock_streaming_enabled=True)
    text = "请看 https://example.com/shot.png 怎么办"
    result = asyncio.run(enrich_user_text_for_agent(text, settings))
    assert "【链接附件解析】" in result
    assert "客户用红框标注" in result
