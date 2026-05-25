#!/usr/bin/env python3
"""Ingest raw/chats session markdown into wiki/sessions and wiki/sources."""

from __future__ import annotations

import argparse
import re
from collections import Counter
from datetime import date
from pathlib import Path

from kb_utils import extract_urls_from_text, is_noise, is_politeness_only

TRANSCRIPT_RE = re.compile(r"- \[[^\]]+\] \*\*([^*]+)\*\*: (.+)$")
SECTION_LINKS = "## 链接与附件内容"

TAG_KEYWORDS: list[tuple[str, list[str]]] = [
    ("高斯", [r"高斯", r"gaussian", r"g16", r"gauss"]),
    ("飞书", [r"飞书", r"feishu", r"openclaw", r"龙虾"]),
    ("资源配置", [r"资源", r"中心", r"队列", r"cpu", r"配置"]),
    ("软件许可", [r"license", r"许可", r"激活", r"安装"]),
    ("作业提交", [r"作业", r"提交", r"排队", r"调度"]),
    ("账户计费", [r"账户", r"充值", r"计费", r"发票", r"余额"]),
    ("网络连接", [r"ssh", r"vpn", r"连接", r"登录", r"无法访问"]),
    ("数据存储", [r"存储", r"上传", r"下载", r"传输", r"文件"]),
]

STEP_HINTS = re.compile(
    r"(第一步|第二步|第三步|点击|打开|进入|选择|在.+?里|按照|步骤|教程|文档|https?://)",
    re.I,
)


def parse_session_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    meta: dict = {"session_id": path.stem.replace("session_", "", 1)}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].splitlines():
                if ":" in line:
                    key, _, val = line.partition(":")
                    meta[key.strip()] = val.strip().strip('"')
            body = parts[2]

    transcript: list[tuple[str, str]] = []
    link_section = ""
    in_dialog = False
    in_links = False
    for line in body.splitlines():
        if line.strip() == "## 对话记录":
            in_dialog = True
            in_links = False
            continue
        if line.strip() == SECTION_LINKS:
            in_dialog = False
            in_links = True
            continue
        if line.startswith("## ") and in_dialog:
            in_dialog = False
        if in_dialog:
            m = TRANSCRIPT_RE.match(line.strip())
            if m:
                transcript.append((m.group(1), m.group(2).strip()))
        elif in_links:
            link_section += line + "\n"

    return {
        "meta": meta,
        "transcript": transcript,
        "link_section": link_section.strip(),
        "raw_path": str(path.as_posix()),
    }


def session_slug(session_id: str) -> str:
    s = session_id.replace(".", "-")
    return re.sub(r"[^\w\-]+", "-", s).strip("-").lower()[:72]


def infer_tags(text: str) -> list[str]:
    tags = []
    lower = text.lower()
    for tag, patterns in TAG_KEYWORDS:
        for pat in patterns:
            if re.search(pat, lower, re.I):
                tags.append(tag)
                break
    return tags[:6]


def substantive_messages(transcript: list[tuple[str, str]], role: str) -> list[str]:
    out = []
    for r, content in transcript:
        if r != role:
            continue
        if is_noise(content) or is_politeness_only(content):
            continue
        if len(content) < 4:
            continue
        out.append(content)
    return out


def truncate(s: str, n: int = 48) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def build_title(visitor_msgs: list[str], tags: list[str]) -> str:
    base = truncate(visitor_msgs[0], 36) if visitor_msgs else "客服会话"
    if tags:
        return f"{base}（{tags[0]}）"
    return base


def build_summary(transcript: list[tuple[str, str]], link_section: str) -> str:
    visitor = substantive_messages(transcript, "访客")
    agent = substantive_messages(transcript, "客服")
    parts = []
    if visitor:
        parts.append(f"访客诉求：{truncate(visitor[0], 80)}")
    if agent:
        parts.append(f"客服处理：{truncate(agent[-1] if len(agent) == 1 else '；'.join(agent[-2:]), 120)}")
    if link_section and "http" in link_section:
        parts.append("会话含内联文档/教程链接，详见下方摘录或原始会话附件区。")
    return " ".join(parts) if parts else "会话含客服与访客实质交流。"


def build_key_bullets(transcript: list[tuple[str, str]], link_section: str) -> list[str]:
    bullets: list[str] = []
    seen: set[str] = set()
    for role in ("访客", "客服"):
        for content in substantive_messages(transcript, role):
            prefix = "访客" if role == "访客" else "客服"
            line = f"{prefix}：{truncate(content, 100)}"
            key = line[:40]
            if key not in seen:
                seen.add(key)
                bullets.append(line)
    urls = extract_urls_from_text(link_section)
    for url in urls[:3]:
        bullets.append(f"参考链接：{url}")
    if link_section and not urls:
        first = link_section.splitlines()
        for ln in first:
            if len(ln.strip()) > 20 and not ln.startswith("#") and not ln.startswith("来源"):
                bullets.append(f"文档摘录：{truncate(ln.strip(), 90)}")
                break
    return bullets[:8]


def build_procedure_bullets(transcript: list[tuple[str, str]], link_section: str) -> list[str]:
    bullets: list[str] = []
    for content in substantive_messages(transcript, "客服"):
        if STEP_HINTS.search(content) or extract_urls_from_text(content):
            bullets.append(truncate(content, 120))
    if link_section:
        for line in link_section.splitlines():
            s = line.strip()
            if s.startswith("第") and "步" in s:
                bullets.append(truncate(s, 100))
    deduped: list[str] = []
    seen: set[str] = set()
    for b in bullets:
        k = b[:30]
        if k not in seen:
            seen.add(k)
            deduped.append(b)
    return deduped[:6]


def assess_value(transcript: list[tuple[str, str]], link_section: str, message_count: int) -> str:
    agent = substantive_messages(transcript, "客服")
    if link_section and len(link_section) > 200:
        return "high"
    if message_count >= 15 or len(agent) >= 4:
        return "high"
    if message_count >= 6 or len(agent) >= 2:
        return "medium"
    return "low"


def render_session_wiki(data: dict, slug: str, today: str) -> str:
    meta = data["meta"]
    transcript = data["transcript"]
    link_section = data["link_section"]
    session_id = meta.get("session_id", slug)
    message_count = int(meta.get("message_count", len(transcript)) or len(transcript))

    visitor_msgs = substantive_messages(transcript, "访客")
    all_text = " ".join(c for _, c in transcript) + " " + link_section
    tags = infer_tags(all_text)
    title = build_title(visitor_msgs, tags)
    value = assess_value(transcript, link_section, message_count)

    tags_yaml = ", ".join(tags) if tags else "客服"
    fm = f"""---
type: session
session_id: "{session_id}"
tags: [{tags_yaml}]
sources:
  - "[[source-session-{slug}]]"
updated_at: {today}
value: {value}
---

# 会话 {truncate(session_id, 40)} — {title}

## 会话摘要
{build_summary(transcript, link_section)}

## 关键信息
"""
    for b in build_key_bullets(transcript, link_section):
        fm += f"- {b}\n"
    if not build_key_bullets(transcript, link_section):
        fm += "- （见对话记录）\n"

    procedures = build_procedure_bullets(transcript, link_section)
    fm += "\n## 操作与政策摘录\n"
    if procedures:
        for p in procedures:
            fm += f"- {p}\n"
    else:
        fm += "- （会话未给出明确操作步骤）\n"

    if visitor_msgs and len(visitor_msgs) > 1:
        fm += "\n## 对话背景\n"
        fm += f"- 访客补充：{truncate('；'.join(visitor_msgs[1:3]), 150)}\n"

    fm += f"\n## 相关\n- [[source-session-{slug}]]\n- 原始：`{data['raw_path']}`\n"
    return fm


def render_source_wiki(data: dict, slug: str, today: str) -> str:
    meta = data["meta"]
    session_id = meta.get("session_id", slug)
    summary = build_summary(data["transcript"], data["link_section"])
    return f"""---
type: source
source_type: chat
source_id: session_{slug}
ingested_at: {today}
---

# 来源：会话 {truncate(session_id, 50)}

## 摘要
{summary}

## 萃取清单
- [[session-{slug}]] — 会话级要点（非 QA）

## 原始路径
`{data['raw_path']}`
"""


def update_index(sessions_dir: Path, sources_dir: Path, today: str) -> str:
    rows = []
    for path in sorted(sessions_dir.glob("*.md")):
        slug = path.stem
        text = path.read_text(encoding="utf-8")
        value = "medium"
        m = re.search(r"^value:\s*(\w+)", text, re.M)
        if m:
            value = m.group(1)
        title_m = re.search(r"^# 会话 .+ — (.+)$", text, re.M)
        title = title_m.group(1) if title_m else slug
        rows.append(f"| [[session-{slug}]] | session ({value}) | 1 | {today} |")

    body = f"""# kefu-know 知识库目录

> 每次 ingest 后更新。最近 ingest：{today}

| 页面 | 类型 | 来源数 | 更新 |
|------|------|--------|------|
"""
    body += "\n".join(rows[:500])
    if len(rows) > 500:
        body += f"\n| … | 另有 {len(rows) - 500} 条 | — | — |"
    body += f"""

## 统计

- wiki/sessions: {len(rows)}
- wiki/sources: {len(list(sources_dir.glob('*.md')))}
- concepts: 0
- procedures: 0
"""
    return body


def update_overview(sessions_dir: Path, today: str) -> str:
    tag_counter: Counter[str] = Counter()
    value_counter: Counter[str] = Counter()
    for path in sessions_dir.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        m = re.search(r"^tags:\s*\[(.+)\]", text, re.M)
        if m:
            for t in re.findall(r"[^,\[\]]+", m.group(1)):
                t = t.strip()
                if t:
                    tag_counter[t] += 1
        vm = re.search(r"^value:\s*(\w+)", text, re.M)
        if vm:
            value_counter[vm.group(1)] += 1

    top_tags = tag_counter.most_common(12)
    lines = [
        "# 知识库总览",
        "",
        f"> 跨来源 living synthesis。更新：{today}",
        "",
        "## 当前状态",
        "",
        f"- 已入库会话：**{len(list(sessions_dir.glob('*.md')))}** 条（来自 Excel 清洗后的 raw/chats）",
        f"- 高价值 (high)：{value_counter.get('high', 0)}",
        f"- 中价值 (medium)：{value_counter.get('medium', 0)}",
        f"- 低价值 (low)：{value_counter.get('low', 0)}",
        "",
        "## 主题索引（按标签频次）",
        "",
    ]
    for tag, cnt in top_tags:
        lines.append(f"- **{tag}**：{cnt} 条会话")
    lines.extend(["", "## 使用说明", "", "- 检索：`@cs-knowledge-wiki query: <问题>`", "- 详情：打开 `wiki/sessions/` 对应页", ""])
    return "\n".join(lines)


def append_log(log_path: Path, today: str, count: int, chats_dir: Path) -> None:
    entry = f"""
## {today} ingest {chats_dir.as_posix()}

- 批量萃取会话 → `wiki/sessions/`：**{count}** 条
- 同步 `wiki/sources/` 来源摘要页
- 更新 `index.md`、`overview.md`
- 格式：会话级要点（非 QA）
"""
    existing = log_path.read_text(encoding="utf-8") if log_path.exists() else "# 操作日志\n\n"
    log_path.write_text(existing.rstrip() + "\n" + entry, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest raw chat sessions into wiki/")
    parser.add_argument("--chats-dir", type=Path, required=True)
    parser.add_argument("--wiki-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    sessions_dir = args.wiki_dir / "sessions"
    sources_dir = args.wiki_dir / "sources"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    sources_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    files = sorted(args.chats_dir.glob("session_*.md"))
    if args.limit > 0:
        files = files[: args.limit]

    ingested = 0
    for path in files:
        data = parse_session_file(path)
        session_id = data["meta"].get("session_id", path.stem)
        slug = session_slug(session_id)

        (sessions_dir / f"{slug}.md").write_text(render_session_wiki(data, slug, today), encoding="utf-8")
        (sources_dir / f"source-session-{slug}.md").write_text(render_source_wiki(data, slug, today), encoding="utf-8")
        ingested += 1

    (args.wiki_dir / "index.md").write_text(update_index(sessions_dir, sources_dir, today), encoding="utf-8")
    (args.wiki_dir / "overview.md").write_text(update_overview(sessions_dir, today), encoding="utf-8")
    append_log(args.wiki_dir / "log.md", today, ingested, args.chats_dir)

    print(f"Done. ingested={ingested} → {sessions_dir}")


if __name__ == "__main__":
    main()
