"""解析客户消息中的 http 链接：图片 OCR、网页正文抽取，再交给 DeepAgent。"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from ipaddress import ip_address
from urllib.parse import urlparse

import httpx

from app.core.settings import Settings
from app.services.local_ocr import ocr_image_url_local

logger = logging.getLogger(__name__)

_VISION_MIN_CHARS = 20

_URL_PATTERN = re.compile(r"https?://[^\s\]\)\"'<>，。；、]+", re.IGNORECASE)
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic", ".heif")
_IMAGE_HOST_HINTS = ("image", "img", "photo", "screenshot", "pic.")

_IMAGE_OCR_PROMPT = (
    "这是客户发来的截图或图片。请完成：\n"
    "1. 识别图中全部可见文字（含 UI 按钮、报错、表格等）；\n"
    "2. **重点描述客户用箭头、框选、圆圈、高亮、手绘标注**标出的位置与所指内容；\n"
    "3. 若有多张叠图或对话气泡，按从上到下、从左到右说明。\n"
    "输出简洁中文，供客服助手作答，不要编造图中不存在的信息。"
)


@dataclass
class AttachmentEnrichment:
    url: str
    kind: str
    content: str
    error: str | None = None


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self._chunks.append(text)

    def get_text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "\n".join(self._chunks))


def extract_http_urls(text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in _URL_PATTERN.finditer(text):
        raw = match.group(0).rstrip(".,;:!?）)]}")
        if raw not in seen:
            seen.add(raw)
            urls.append(raw)
    return urls


def _is_private_host(hostname: str) -> bool:
    lowered = hostname.lower()
    if lowered in {"localhost", "127.0.0.1", "::1"} or lowered.endswith(".local"):
        return True
    try:
        ip = ip_address(hostname)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False


def is_safe_http_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.hostname or ""
    if not host:
        return False
    return not _is_private_host(host)


def guess_url_kind(url: str) -> str:
    path = urlparse(url).path.lower()
    if any(path.endswith(suffix) for suffix in _IMAGE_SUFFIXES):
        return "image"
    host = (urlparse(url).hostname or "").lower()
    if any(hint in host for hint in _IMAGE_HOST_HINTS):
        return "image"
    return "page"


async def _probe_content_type(url: str, timeout: float) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.head(url)
            if response.status_code >= 400:
                response = await client.get(url, headers={"Range": "bytes=0-0"})
            return (response.headers.get("content-type") or "").split(";")[0].strip().lower()
    except Exception:
        return None


async def classify_url(url: str, *, timeout: float) -> str:
    kind = guess_url_kind(url)
    if kind == "image":
        return "image"
    content_type = await _probe_content_type(url, timeout)
    if content_type and content_type.startswith("image/"):
        return "image"
    return "page"


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.get_text()


async def fetch_page_text(url: str, *, settings: Settings) -> str:
    timeout = settings.link_enrichment_timeout_seconds
    max_bytes = settings.link_enrichment_max_page_bytes
    max_chars = settings.link_enrichment_max_page_chars
    headers = {"User-Agent": settings.link_enrichment_user_agent}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        content_type = (response.headers.get("content-type") or "").lower()
        if "html" not in content_type and "text/plain" not in content_type:
            return f"（非 HTML 页面，Content-Type: {content_type or 'unknown'}）"
        raw = response.content[:max_bytes]
        html = raw.decode(response.encoding or "utf-8", errors="replace")
    text = html_to_text(html)
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n…（页面正文已截断）"
    return text or "（未能从页面提取到正文）"


def _build_vision_client(settings: Settings):
    from langchain_openai import ChatOpenAI

    model = settings.link_enrichment_vision_model.strip() or settings.deepagent_ark_model
    return ChatOpenAI(
        model=model,
        api_key=settings.deepagent_ark_api_key.get_secret_value(),
        base_url=settings.deepagent_ark_base_url,
        temperature=0.0,
        timeout=settings.link_enrichment_timeout_seconds,
        max_tokens=2048,
    )


def _normalize_vision_content(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts).strip()
    return str(content).strip()


async def _ocr_image_url_vision(url: str, *, settings: Settings) -> str:
    llm = _build_vision_client(settings)

    def _invoke() -> str:
        response = llm.invoke(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _IMAGE_OCR_PROMPT},
                        {"type": "image_url", "image_url": {"url": url}},
                    ],
                }
            ]
        )
        return _normalize_vision_content(response.content)

    return await asyncio.to_thread(_invoke)


async def _ocr_image_url_local_fallback(url: str, *, settings: Settings, reason: str) -> str:
    if not settings.link_enrichment_local_ocr_fallback_enabled:
        raise RuntimeError(f"视觉 OCR 不可用且未启用本地兜底: {reason}")
    try:
        local_text = await ocr_image_url_local(url, settings=settings)
        return f"【本地 RapidOCR 兜底】{reason}\n\n{local_text}"
    except ImportError as exc:
        logger.warning("local ocr unavailable url=%s: %s", url, exc)
        return f"（视觉 OCR 失败: {reason}；本地 OCR 未安装 rapidocr-onnxruntime）"
    except Exception as exc:
        logger.warning("local ocr failed url=%s: %s", url, exc)
        return f"（视觉 OCR 失败: {reason}；本地 OCR 失败: {exc}）"


async def ocr_image_url(url: str, *, settings: Settings) -> str:
    if settings.mock_streaming_enabled:
        return "（mock 模式：未调用视觉 OCR）"

    has_ark_key = bool(settings.deepagent_ark_api_key.get_secret_value())
    if not has_ark_key:
        return await _ocr_image_url_local_fallback(url, settings=settings, reason="未配置 Ark API Key")

    vision_text = ""
    vision_error: str | None = None
    try:
        vision_text = await _ocr_image_url_vision(url, settings=settings)
        if len(vision_text) >= _VISION_MIN_CHARS:
            return f"【视觉模型 OCR】\n{vision_text}"
        vision_error = "视觉模型返回内容过短"
        logger.info("vision ocr short result url=%s len=%s", url[:80], len(vision_text))
    except Exception as exc:
        vision_error = str(exc)
        logger.warning("vision ocr failed url=%s: %s", url, exc)

    fallback_reason = vision_error or "视觉模型结果过短"
    if vision_text:
        fallback_reason = f"{fallback_reason}；视觉片段: {vision_text[:200]}"
    return await _ocr_image_url_local_fallback(url, settings=settings, reason=fallback_reason)


async def enrich_attachment(url: str, *, settings: Settings) -> AttachmentEnrichment:
    if not is_safe_http_url(url):
        return AttachmentEnrichment(url=url, kind="blocked", content="", error="不允许访问的内网或非法 URL")

    try:
        kind = await classify_url(url, timeout=settings.link_enrichment_timeout_seconds)
        if kind == "image":
            text = await ocr_image_url(url, settings=settings)
            return AttachmentEnrichment(url=url, kind="image", content=text)
        text = await fetch_page_text(url, settings=settings)
        return AttachmentEnrichment(url=url, kind="page", content=text)
    except Exception as exc:
        logger.warning("enrich attachment failed url=%s: %s", url, exc)
        return AttachmentEnrichment(url=url, kind="error", content="", error=str(exc))


def format_enriched_message(user_text: str, attachments: list[AttachmentEnrichment]) -> str:
    if not attachments:
        return user_text

    lines = [
        "【客户原始消息】",
        user_text.strip(),
        "",
        "【链接附件解析】（以下由系统自动抓取/OCR，请结合客户问题作答；图片请特别关注客户标注位置）",
    ]
    for index, item in enumerate(attachments, start=1):
        if item.error:
            lines.extend([f"### 附件 {index}：{item.url}", f"类型：{item.kind}，解析失败：{item.error}", ""])
            continue
        type_label = "图片 OCR" if item.kind == "image" else "网页正文"
        lines.extend([f"### 附件 {index}：{item.url}", f"类型：{type_label}", item.content.strip(), ""])
    lines.append(
        "请根据上述附件内容理解客户意图；回答产品问题时仍须调用 query_kefu_wiki，"
        "附件仅作上下文，勿编造附件中未出现的信息。"
    )
    return "\n".join(lines).strip()


async def enrich_user_text_for_agent(user_text: str, settings: Settings | None = None) -> str:
    """检测 http(s) 链接并 enrich；无链接或未开启时返回原文。"""
    resolved = settings or Settings()
    if not resolved.link_enrichment_enabled:
        return user_text

    urls = extract_http_urls(user_text)
    if not urls:
        return user_text

    limited = urls[: resolved.link_enrichment_max_urls]
    tasks = [enrich_attachment(url, settings=resolved) for url in limited]
    attachments = await asyncio.gather(*tasks)
    enriched = format_enriched_message(user_text, list(attachments))
    logger.info(
        "user link enrichment: urls=%s kinds=%s",
        len(limited),
        [f"{item.kind}:{bool(item.content)}" for item in attachments],
    )
    return enriched
