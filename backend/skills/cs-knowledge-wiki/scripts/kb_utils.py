"""Shared helpers for cs-knowledge-wiki scripts."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico", ".heic"}

URL_IN_TEXT_RE = re.compile(r"https?://[^\s\]<>)\"\'\u4e00-\u9fff，。；、]+", re.I)
EMOJI_ONLY_RE = re.compile(r"^(\[[^\]]+\])+$")

NOISE_PATTERNS = [
    r"访客离开超时",
    r"会话已关闭",
    r"访客已离开",
    r"访客接入座席",
    r"座席.+关闭会话",
    r"转接会话",
    r"将会话转接",
    r"您还未回复用户消息",
    r"的转接会话接入",
    r"您还未回复访客",
    r"^好的$",
    r"^谢谢",
    r"^在吗",
    r"^您好$",
    r"^nan$",
    r"^\[咖啡\]$",
    r"^\[玫瑰\]$",
    r"^\[强\]$",
    r"^收到$",
    r"^嗯嗯?$",
    r"^ok$",
    r"^可以的$",
]

POLITENESS_PATTERNS = [
    r"多谢",
    r"谢谢",
    r"不客气",
    r"^\[抱拳\]",
    r"到时候不懂可以问",
]

OCR_LOW_VALUE_PATTERNS = [
    r"^[_\-\s\d\.\:：]+$",
    r"^第\s*\d+\s*页",
    r"^截图",
    r"^图片$",
]


INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n]')
ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")
MOJIBAKE_HINT_RE = re.compile(r"(?:Ã.|Â.|â.|æ.|å.|è.|é.|ê.|ë.)")


def repair_mojibake(text: str) -> str:
    """修复 UTF-8 被误按 Latin-1 解码导致的乱码。"""
    if not text or not MOJIBAKE_HINT_RE.search(text):
        return text
    try:
        fixed = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    cjk_before = len(re.findall(r"[\u4e00-\u9fff]", text))
    cjk_after = len(re.findall(r"[\u4e00-\u9fff]", fixed))
    if cjk_after > cjk_before:
        return fixed
    return text


def normalize_text(text: str) -> str:
    """统一空白、去除零宽字符并尝试修复乱码。"""
    if not text:
        return ""
    text = repair_mojibake(text)
    text = ZERO_WIDTH_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_page_title(title: str) -> str:
    """去掉站点后缀、零宽字符等。"""
    title = normalize_text(title or "")
    if not title:
        return ""
    title = re.split(r"\s*[|｜]\s*", title, maxsplit=1)[0].strip()
    return title


def sanitize_filename(text: str, max_len: int = 72) -> str:
    text = re.sub(
        r"[\U00010000-\U0010ffff\u2600-\u27bf\ufe0f\u200d]",
        "",
        text,
    )
    text = INVALID_FILENAME_CHARS.sub("", text)
    text = re.sub(r"\s+", "-", text.strip())
    text = re.sub(r"-{2,}", "-", text).strip("- .")
    if len(text) > max_len:
        text = text[:max_len].rstrip("-的了吗呢")
    return text or "未命名"


def slug_from_url(url: str) -> str:
    digest = hashlib.md5(url.encode()).hexdigest()[:10]
    path_slug = re.sub(r"[^\w\-]+", "-", url.split("?")[0].rstrip("/").split("/")[-1]).strip("-").lower()
    return f"{path_slug or 'url'}-{digest}"


def extension_from_url(url: str) -> str:
    parsed = urlparse(url)
    path = unquote(parsed.path).lower()
    match = re.search(r"(\.[a-z0-9]+)(?:\?|$)", path, re.I)
    if match:
        return match.group(1).lower()
    qs = parse_qs(parsed.query)
    for key in ("fileName", "filename", "file_name"):
        if key in qs and qs[key]:
            name = unquote(qs[key][0]).lower()
            m = re.search(r"(\.[a-z0-9]+)$", name, re.I)
            if m:
                return m.group(1).lower()
    return ""


def is_image_url(url: str, content_type: str = "") -> bool:
    ext = extension_from_url(url)
    if ext in IMAGE_EXTENSIONS:
        return True
    if content_type.startswith("image/"):
        return True
    return False


def extract_urls_from_text(text: str) -> list[str]:
    if not text:
        return []
    urls = []
    for match in URL_IN_TEXT_RE.finditer(text):
        u = match.group(0).rstrip(".,;:)】」")
        if u.startswith("http"):
            urls.append(u)
    return urls


def is_noise(text: str) -> bool:
    text = (text or "").strip()
    if len(text) < 2:
        return True
    if text.lower() == "nan":
        return True
    if EMOJI_ONLY_RE.match(text):
        return True
    for pat in NOISE_PATTERNS:
        if re.search(pat, text, re.I):
            return True
    return False


def is_politeness_only(text: str) -> bool:
    text = (text or "").strip()
    if not text or EMOJI_ONLY_RE.match(text):
        return True
    for pat in POLITENESS_PATTERNS:
        if re.search(pat, text, re.I):
            return True
    return False


def is_meaningful_text(text: str, min_chars: int = 20) -> bool:
    if not text or not text.strip():
        return False

    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("_fetched_at:") or s.startswith("## 图片文字识别"):
            continue
        if s.startswith("_未识别") or s.startswith(">"):
            continue
        lines.append(s)

    body = "\n".join(lines).strip()
    if len(body) < min_chars:
        return False

    for pat in OCR_LOW_VALUE_PATTERNS:
        if re.match(pat, body, re.I):
            return False

    substantive_lines = 0
    for line in lines:
        if is_noise(line) or is_politeness_only(line):
            continue
        if len(re.findall(r"[\u4e00-\u9fff]", line)) >= 4:
            substantive_lines += 1
        elif len(re.findall(r"[A-Za-z]{4,}", line)) >= 1:
            substantive_lines += 1
        elif len(line) >= 12 and re.search(r"[\u4e00-\u9fffA-Za-z0-9]", line):
            substantive_lines += 1

    return substantive_lines >= 1


def _score_substantive_message(role: str, content: str) -> int:
    """单条消息实质度：0=无价值，1=一般，2=含链接。"""
    if extract_urls_from_text(content):
        return 2
    chinese = len(re.findall(r"[\u4e00-\u9fff]", content))
    if role == "访客" and chinese >= 5:
        return 1
    if role == "客服" and chinese >= 6:
        return 1
    if len(content) >= 10:
        return 1
    return 0


def is_session_meaningful(transcript_lines: list[str]) -> bool:
    """会话需有客服实质回复；仅访客发言、纯寒暄/致谢/表情会话丢弃。"""
    has_meaningful_agent = False
    substantive_score = 0

    for line in transcript_lines:
        match = re.match(r"- \[[^\]]+\] \*\*([^*]+)\*\*: (.+)$", line.strip())
        if not match:
            continue
        role, content = match.group(1), match.group(2).strip()
        if role not in ("访客", "客服"):
            continue
        if is_noise(content) or is_politeness_only(content):
            continue

        score = _score_substantive_message(role, content)
        if score <= 0:
            continue
        substantive_score += score
        if role == "客服":
            has_meaningful_agent = True

    return has_meaningful_agent and substantive_score >= 1


def parse_frontmatter_urls(text: str) -> list[str]:
    if not text.startswith("---"):
        return []
    parts = text.split("---", 2)
    if len(parts) < 3:
        return []
    fm = parts[1]
    urls: list[str] = []
    in_urls = False
    for line in fm.splitlines():
        if line.strip() == "file_urls:":
            in_urls = True
            continue
        if in_urls:
            if line.startswith("  - "):
                u = line[4:].strip().strip('"').strip("'")
                if u.startswith("http"):
                    urls.append(u)
            elif not line.startswith("  "):
                in_urls = False
    return urls


def collect_all_urls_from_session_text(text: str) -> list[str]:
    """frontmatter file_urls + 对话/附件区中的 http 链接。"""
    urls = list(parse_frontmatter_urls(text))
    seen = set(urls)
    for u in extract_urls_from_text(text):
        if u not in seen:
            urls.append(u)
            seen.add(u)
    return urls


def collect_urls_from_sessions(chats_dir: Path) -> list[str]:
    urls: list[str] = []
    for path in chats_dir.glob("session_*.md"):
        urls.extend(collect_all_urls_from_session_text(path.read_text(encoding="utf-8")))
    return list(dict.fromkeys(urls))
