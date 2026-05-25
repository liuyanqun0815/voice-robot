#!/usr/bin/env python3
"""Crawl a website (same-domain BFS) and save pages as markdown under output dir."""

from __future__ import annotations

import argparse
import hashlib
import re
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from kb_utils import clean_page_title, normalize_text, repair_mojibake, sanitize_filename

try:
    import trafilatura
except ImportError:
    trafilatura = None  # type: ignore

USER_AGENT = "cs-knowledge-wiki/1.0 (+https://github.com/SamurAIGPT/llm-wiki-agent)"


def safe_print(message: str) -> None:
    """Windows 控制台 GBK 下避免 emoji 等字符导致崩溃。"""
    try:
        print(message)
    except UnicodeEncodeError:
        print(message.encode("gbk", errors="replace").decode("gbk"))
SKIP_EXTENSIONS = {".pdf", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".mp4", ".mp3"}
PATH_SKIP_PARTS = {"ac", "openapi", "doc", "2.0", "api", "moduleapi", "html", "help", "docs", "mainsite"}
# 侧栏/站点级 h2，不作为文件名核心
NAV_H2_SKIP = {
    "平台功能API",
    "计算服务",
    "快速入门",
    "认证授权",
    "用户资源",
    "作业",
    "文件",
    "容器",
    "Notebook",
    "桌面",
}

# URL 路径片段 → 中文标签（便于生成语义化文件名）
PATH_LABELS: dict[str, str] = {
    "apicall": "API调用",
    "apikeymgt": "API-Key管理",
    "modulelist": "模型列表",
    "token": "Token计费",
    "errorcode": "错误码",
    "overview": "概述",
    "quickstart": "快速开始",
    "chat": "对话补全",
    "embedding": "Embedding",
    "ocr": "OCR",
    "codingplan": "CodingPlan",
    "callbytools": "第三方工具接入",
    "safecertification": "认证授权",
    "jobmanager": "作业管理",
    "efile": "文件传输",
    "container": "容器",
    "notebook": "Notebook",
    "desktop": "桌面VNC",
    "tutorials": "教程",
    "readme": "说明",
}


def decode_response_html(resp: requests.Response) -> str:
    """按响应头 / meta / 常见中文编码解码 HTML。"""
    raw = resp.content
    if not raw:
        return ""

    header_enc = (resp.encoding or "").strip().lower()
    if header_enc and header_enc not in ("iso-8859-1", "latin-1", "ascii"):
        try:
            return raw.decode(header_enc)
        except (UnicodeDecodeError, LookupError):
            pass

    for enc in ("utf-8", "gb18030", "gbk", "gb2312"):
        try:
            text = raw.decode(enc)
            if enc != "utf-8" or "\ufffd" not in text:
                return text
        except UnicodeDecodeError:
            continue

    return raw.decode("utf-8", errors="replace")


def path_segment_label(segment: str) -> str:
    segment = re.sub(r"\.html?$", "", segment, flags=re.I)
    key = segment.lower()
    if key in PATH_LABELS:
        return PATH_LABELS[key]
    segment = re.sub(r"([a-z])([A-Z])", r"\1-\2", segment)
    return segment.replace("_", "-")


def path_to_label(url: str) -> str:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p and p.lower() not in PATH_SKIP_PARTS]
    if not parts:
        return "首页"
    labels = [path_segment_label(p) for p in parts[-2:]]
    return "-".join(labels)


def first_content_h2(body_md: str, soup: BeautifulSoup) -> str | None:
    """取正文首个有意义的 h2（优先 markdown 正文，跳过侧栏标题）。"""
    for line in body_md.splitlines():
        match = re.match(r"^##\s+(.+)$", line.strip())
        if match:
            text = clean_page_title(match.group(1))
            if text and text not in NAV_H2_SKIP:
                return text

    for el in soup.find_all("h2"):
        text = clean_page_title(el.get_text(" ", strip=True))
        if text and text not in NAV_H2_SKIP:
            return text
    return None


def build_core_filename(title: str, url: str, body_md: str, soup: BeautifulSoup, used: set[str]) -> str:
    """根据标题与正文小节生成体现核心内容的文件名。"""
    core_title = clean_page_title(title)
    core_h2 = first_content_h2(body_md, soup) or ""
    path_hint = path_segment_label(urlparse(url).path.rstrip("/").split("/")[-1])

    if core_h2 and core_h2 not in core_title:
        base = f"{core_title}-{core_h2}" if core_title else core_h2
    elif path_hint and path_hint not in (core_title, "html") and core_title:
        base = f"{core_title}-{path_hint}"
    elif core_title:
        base = core_title
    else:
        base = path_to_label(url)

    name = sanitize_filename(base)
    if name not in used:
        used.add(name)
        return name

    suffix = hashlib.md5(url.encode()).hexdigest()[:6]
    candidate = sanitize_filename(f"{base}-{suffix}")
    n = 2
    while candidate in used:
        candidate = sanitize_filename(f"{base}-{suffix}{n}")
        n += 1
    used.add(candidate)
    return candidate


def same_domain(base: str, candidate: str) -> bool:
    return urlparse(base).netloc == urlparse(candidate).netloc


def path_allowed(url: str, path_prefix: str) -> bool:
    if not path_prefix:
        return True
    return urlparse(url).path.startswith(path_prefix)


def fetch_robots(base_url: str) -> RobotFileParser | None:
    parsed = urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = RobotFileParser()
    try:
        rp.set_url(robots_url)
        rp.read()
        return rp
    except Exception:
        return None


def first_heading_text(soup: BeautifulSoup, tag: str) -> str:
    el = soup.find(tag)
    return normalize_text(el.get_text(" ", strip=True)) if el else ""


def extract_main_text(html: str, url: str) -> tuple[str, str, str | None]:
    """返回 (title, markdown_body, content_h2)。"""
    html = repair_mojibake(html)
    soup = BeautifulSoup(html, "lxml")
    title = first_heading_text(soup, "title") or urlparse(url).path

    body_md = ""
    if trafilatura:
        downloaded = trafilatura.extract(html, url=url, include_comments=False, include_tables=True)
        if downloaded and len(downloaded.strip()) > 80:
            body_md = normalize_text(downloaded)

    if not body_md or len(body_md) < 80:
        work = BeautifulSoup(html, "lxml")
        for tag in work(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        main = work.find("article") or work.find("main") or work.find(class_=re.compile(r"content|article|doc", re.I))
        body = main or work.body
        if not body:
            return clean_page_title(title), "", None

        lines: list[str] = []
        for el in body.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "code"]):
            text = normalize_text(el.get_text("\n", strip=True))
            if not text:
                continue
            if el.name == "h1":
                lines.append(f"# {text}")
            elif el.name == "h2":
                lines.append(f"## {text}")
            elif el.name == "h3":
                lines.append(f"### {text}")
            elif el.name == "h4":
                lines.append(f"#### {text}")
            elif el.name == "li":
                lines.append(f"- {text}")
            elif el.name in ("pre", "code") and "\n" in text:
                lines.append(f"```\n{text}\n```")
            else:
                lines.append(text)
        body_md = "\n\n".join(lines)

    body_md = normalize_text(body_md)
    title = clean_page_title(title) or path_to_label(url)

    # 去掉与标题重复的顶层 h1
    if body_md.startswith(f"# {title}"):
        body_md = body_md[len(f"# {title}") :].lstrip()

    # 去掉连续重复标题行
    body_md = re.sub(rf"^(#+\s*{re.escape(title)}\s*\n)+", "", body_md, flags=re.I).strip()

    content_h2 = first_content_h2(body_md, soup)
    return title, body_md, content_h2


def should_skip_url(url: str, allow_query: bool) -> bool:
    parsed = urlparse(url)
    if any(parsed.path.lower().endswith(ext) for ext in SKIP_EXTENSIONS):
        return True
    if not allow_query and parsed.query:
        return True
    lower = url.lower()
    for segment in ("/login", "/logout", "/signin", "/signup"):
        if segment in lower:
            return True
    return False


def crawl(
    base_url: str,
    output_dir: Path,
    max_depth: int,
    delay: float,
    allow_query: bool,
    clean: bool,
    path_prefix: str = "",
) -> int:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    robots = fetch_robots(base_url)
    seen: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(base_url, 0)])
    saved = 0
    used_names: set[str] = set()

    output_dir.mkdir(parents=True, exist_ok=True)
    if clean:
        for old in output_dir.glob("*.md"):
            old.unlink()

    while queue:
        url, depth = queue.popleft()
        url, _ = urldefrag(url)
        if url in seen or depth > max_depth:
            continue
        if should_skip_url(url, allow_query):
            continue
        if not same_domain(base_url, url):
            continue
        if not path_allowed(url, path_prefix):
            continue
        if robots and not robots.can_fetch(USER_AGENT, url):
            continue

        seen.add(url)
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
        except Exception as exc:
            safe_print(f"SKIP {url}: {exc}")
            continue

        content_type = resp.headers.get("Content-Type", "")
        if "text/html" not in content_type:
            continue

        html = decode_response_html(resp)
        title, text, content_h2 = extract_main_text(html, url)
        soup = BeautifulSoup(html, "lxml")
        if len(text.strip()) < 50:
            safe_print(f"SKIP (thin): {url}")
        else:
            fname = build_core_filename(title, url, text, soup, used_names)
            out_path = output_dir / f"{fname}.md"
            fetched_at = datetime.now(timezone.utc).isoformat()
            frontmatter = (
                f"---\nsource_type: website\nsource_url: {url}\n"
                f"fetched_at: {fetched_at}\ntitle: {title}\n"
                f"filename_core: {fname}\n---\n\n"
            )
            body = f"# {title}\n\n"
            if content_h2 and content_h2 not in title:
                body += f"> 章节：{content_h2}\n\n"
            body += text + "\n"
            out_path.write_text(frontmatter + body, encoding="utf-8")
            safe_print(f"SAVED {out_path.name} <- {url}")
            saved += 1

        if depth < max_depth:
            soup = BeautifulSoup(html, "lxml")
            for a in soup.find_all("a", href=True):
                link = urljoin(url, a["href"])
                link, _ = urldefrag(link)
                if (
                    link.startswith("http")
                    and same_domain(base_url, link)
                    and path_allowed(link, path_prefix)
                ):
                    queue.append((link, depth + 1))

        time.sleep(delay)

    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl website into raw markdown.")
    parser.add_argument("--base-url", required=True, help="Start URL (same domain only)")
    parser.add_argument("--output", required=True, help="Output directory, e.g. raw/websites/")
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--allow-query", action="store_true")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="抓取前清空输出目录中的旧 .md 文件",
    )
    parser.add_argument(
        "--path-prefix",
        default="",
        help="仅抓取路径以此前缀开头的 URL，如 /help/docs/",
    )
    args = parser.parse_args()

    count = crawl(
        args.base_url,
        Path(args.output),
        args.max_depth,
        args.delay,
        args.allow_query,
        args.clean,
        args.path_prefix,
    )
    safe_print(f"Done. Saved {count} pages.")


if __name__ == "__main__":
    main()
