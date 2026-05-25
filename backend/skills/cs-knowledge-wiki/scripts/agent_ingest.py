#!/usr/bin/env python3
"""Agent 侧大模型萃取约定：脚本只负责队列导出与结果合并，不在脚本内调用 LLM API。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AGENT_SESSION_PROMPT = """你是超算互联网客服知识库编辑。

【必须】先完整阅读 manifest 中指定的 source_file（含 ## 对话记录、## 链接与附件内容），再输出**仅一行 JSON**：

{"skip": false, "summary": "本会话 1～3 句摘要", "category_id": "英文短横线id", "category_title": "中文分类名", "question": "标准问法", "question_variants": [], "answer": "完整回答", "steps": ["操作步骤..."], "tags": [], "related_wikilinks": ["概念-Slurm", "问答-xxx"]}

纯寒暄、无实质客服回复、仅系统通知时：{"skip": true}

要求：
- 必须先读原文再写；禁止仅凭 manifest preview 臆造
- answer 整合客服口径与「链接与附件内容」
- related_wikilinks 填已有或拟建 wiki 页名（不含 .md），便于交叉引用
- 若 wiki 已有 taxonomy，category_id 与其 categories.id 对齐
- 不要输出 markdown 代码块外的文字
"""

AGENT_TAXONOMY_PROMPT = """你是知识库信息架构师。

【必须】对 doc_list 中每一篇：打开对应 raw/websites/*.md **通读全文**后再分类；清单索引不能代替阅读原文。

先阅读已有 wiki/index.md、wiki/taxonomy.json（若存在），再设计分类与概念；同 source_url 的条目视为**更新**而非新建。

输出 JSON（不要 markdown 外壳）：
{
  "source": "cursor-agent",
  "categories": [{"id": "hpc-jobs", "title": "高性能计算与作业", "overview": "主题概述..."}],
  "concepts": [{"id": "slurm", "title": "Slurm", "definition": "...", "related_doc_indices": [0, 2]}],
  "assignments": [{"source_url": "https://...", "category_id": "hpc-jobs"}],
  "doc_enrichments": [
    {"source_url": "https://...", "summary": "1～3句摘要", "related_concept_ids": ["slurm"], "related_doc_urls": ["https://..."]}
  ]
}

要求：
- 分类 6～14 个；概念 6～15 个
- assignments 覆盖每一篇（source_url 或 doc_index）
- doc_enrichments 每篇至少一条 summary；关联同主题/上下游文档 URL
"""


def parse_json_response(text: str) -> Any:
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.I | re.M)
    return json.loads(raw)


def session_id_from_path(path: Path) -> str:
    """与 load_session_extractions 一致：session_<uuid>-<timestamp> → <uuid>。"""
    name = path.stem
    if name.startswith("session_"):
        rest = name.replace("session_", "", 1)
        parts = rest.split("-")
        if len(parts) >= 6:
            return "-".join(parts[:-1])
        return rest
    return name


def export_session_queue(
    chats_dir: Path,
    queue_dir: Path,
    session_to_text: Any,
    limit: int = 0,
) -> int:
    """导出待 Agent 萃取的会话队列。"""
    extractions_dir = queue_dir / "extractions"
    extractions_dir.mkdir(parents=True, exist_ok=True)

    pending: list[dict[str, str]] = []
    files = sorted(chats_dir.glob("session_*.md"))
    if limit > 0:
        files = files[:limit]

    for path in files:
        sid = session_id_from_path(path)
        out_path = extractions_dir / f"{sid}.json"
        if out_path.exists():
            continue
        data = session_to_text(path) if callable(session_to_text) else {}
        pending.append(
            {
                "session_id": sid,
                "source_file": str(path.as_posix()),
                "output_file": str(out_path.as_posix()),
                "preview": (data.get("preview", "") if isinstance(data, dict) else "")[:200],
            }
        )

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "role": "cursor-agent",
        "instruction": "由上层 Agent 先通读各 source_file 原文，再分类/摘要/关联，写入 output_file；禁止在 Python 脚本内调用 OpenAI API。",
        "prompt": AGENT_SESSION_PROMPT,
        "pending_count": len(pending),
        "pending": pending,
    }
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (queue_dir / "PROMPT.md").write_text(
        f"# Agent 会话萃取说明\n\n{AGENT_SESSION_PROMPT}\n",
        encoding="utf-8",
    )
    return len(pending)


def load_session_extractions(
    extractions_dir: Path,
    chats_dir: Path,
    parse_session_file: Any,
    parse_link_section: Any,
    infer_category_id: Any,
    categories: dict[str, Any] | None = None,
) -> list[tuple[dict, dict]]:
    """读取 Agent 写入的 extractions/*.json，返回 (session_data, extraction) 列表。"""
    results: list[tuple[dict, dict]] = []
    if not extractions_dir.is_dir():
        return results

    cat_by_title = {}
    if categories:
        for cid, cat in categories.items():
            title = cat.title if hasattr(cat, "title") else cat.get("title", "")
            cat_by_title[title] = cid

    for json_path in sorted(extractions_dir.glob("*.json")):
        try:
            ext = parse_json_response(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if ext.get("skip"):
            continue

        sid = json_path.stem
        matches = list(chats_dir.glob(f"session_{sid}*.md"))
        if not matches:
            matches = list(chats_dir.glob(f"session_{sid}.md"))
        if not matches:
            continue
        session_path = matches[0]

        data = parse_session_file(session_path)
        cid = ext.get("category_id", "")
        if not cid and ext.get("category_title"):
            cid = cat_by_title.get(ext["category_title"], "")
        if not cid and categories and ext.get("category") in categories:
            cid = ext["category"]
        if not cid:
            blob = ext.get("question", "") + ext.get("answer", "")
            cid = infer_category_id(blob) if infer_category_id else "general"

        if not ext.get("link_blocks") and data.get("link_section"):
            ext["link_blocks"] = parse_link_section(data["link_section"])

        ext["category_id"] = cid
        results.append((data, ext))

    return results


def export_taxonomy_request(
    docs: list[Any],
    queue_dir: Path,
    format_doc_line: Any,
    max_docs: int = 200,
) -> int:
    """导出网站文档清单，供 Agent 生成 wiki/taxonomy.json。"""
    queue_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, doc in enumerate(docs[:max_docs]):
        line = format_doc_line(i, doc)
        url = getattr(doc, "source_url", "")
        if url:
            line += f"\n   source_url: {url}"
        lines.append(line)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "role": "cursor-agent",
        "doc_count": min(len(docs), max_docs),
        "output_file": "wiki/taxonomy.json",
        "instruction": "由上层 Agent 逐篇阅读 raw 原文后生成 taxonomy.json（含 doc_enrichments 摘要与关联），写入 output_file。禁止在 ingest 脚本内调 API。",
        "prompt": AGENT_TAXONOMY_PROMPT,
        "doc_list": lines,
    }
    (queue_dir / "taxonomy-request.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (queue_dir / "PROMPT.md").write_text(
        f"# Agent 分类/概念生成说明\n\n{AGENT_TAXONOMY_PROMPT}\n",
        encoding="utf-8",
    )
    return len(lines)


def load_taxonomy_file(
    taxonomy_path: Path,
) -> tuple[dict[str, Any], list[Any], dict[str, str], dict[int, str], dict[str, Any]]:
    """加载 Agent 生成的 taxonomy.json。返回 (categories, concepts, by_url, by_index, raw_data)。"""
    from wiki_taxonomy_llm import parse_categories, parse_concepts

    data = parse_json_response(taxonomy_path.read_text(encoding="utf-8"))
    categories = parse_categories(data)
    concepts = parse_concepts(data)
    by_url: dict[str, str] = {}
    by_index: dict[int, str] = {}
    for item in data.get("assignments", []):
        cid = str(item.get("category_id", "")).strip()
        if not cid:
            continue
        if item.get("source_url"):
            by_url[str(item["source_url"]).strip()] = cid
        if "doc_index" in item:
            try:
                by_index[int(item["doc_index"])] = cid
            except (TypeError, ValueError):
                pass
    return categories, concepts, by_url, by_index, data


def render_extraction_markdown(
    ext: dict,
    category_link: str,
    link_blocks: list | None = None,
    today: str = "",
) -> str:
    """按 SKILL 约定渲染单条 FAQ 草稿 markdown（含 wikilink）。"""
    from datetime import date

    today = today or date.today().isoformat()
    question = ext.get("question", "未命名问题")
    cat_display = ext.get("category_title", category_link)
    cat_wl = category_link if category_link else cat_display
    body = f"""---
type: faq
source: cursor-agent
category: "[[{cat_wl}]]"
tags: [{cat_display}]
source_session_id: {ext.get("source_session_id", "")}
updated_at: {today}
---

# {question}

## 会话摘要

{ext.get("summary", "")}

## 客户问法

"""
    for v in [question, *(ext.get("question_variants") or [])]:
        if v:
            body += f"- {v}\n"

    body += f"\n## 标准回答\n\n{ext.get('answer', '').strip()}\n"

    if link_blocks:
        body += "\n## 链接与附件内容（会话内解析）\n\n"
        for block in link_blocks:
            title = block.title if hasattr(block, "title") else block.get("title", "")
            url = block.url if hasattr(block, "url") else block.get("url", "")
            kind = block.kind if hasattr(block, "kind") else block.get("kind", "网页")
            bbody = block.body if hasattr(block, "body") else block.get("body", "")
            body += f"### [{kind}] {title}\n\n"
            if url:
                body += f"来源: {url}\n\n"
            if bbody:
                body += bbody[:2000].strip() + "\n\n"

    steps = ext.get("steps") or []
    if steps:
        body += "## 操作步骤\n"
        for i, s in enumerate(steps, 1):
            body += f"{i}. {s}\n"

    body += "\n## 相关\n"
    if cat_wl:
        body += f"- [[{cat_wl}]]\n"
    for link in ext.get("related_wikilinks") or []:
        link = str(link).strip().removesuffix(".md")
        if link and link != cat_wl:
            body += f"- [[{link}]]\n"
    return body


def apply_taxonomy_assignments(
    docs: list[Any],
    by_url: dict[str, str],
    by_index: dict[int, str],
    categories: dict[str, Any],
) -> None:
    fallback = "general" if "general" in categories else next(iter(categories))
    for i, doc in enumerate(docs):
        url = getattr(doc, "source_url", "") or ""
        cid = by_url.get(url) or by_index.get(i) or getattr(doc, "category_id", "") or fallback
        if cid not in categories:
            cid = fallback
        doc.category_id = cid


def apply_doc_enrichments(docs: list[Any], data: dict) -> None:
    """将 Agent 写入 taxonomy 的 per-doc 摘要与关联应用到 DocItem。"""
    by_url = {getattr(d, "source_url", "") or "": d for d in docs}
    for item in data.get("doc_enrichments", []):
        url = str(item.get("source_url", "")).strip()
        if not url or url not in by_url:
            continue
        doc = by_url[url]
        summary = str(item.get("summary", "")).strip()
        if summary:
            doc.summary = summary
        related_urls = item.get("related_doc_urls") or item.get("related_urls") or []
        names: list[str] = list(getattr(doc, "related_doc_names", None) or [])
        for rel_url in related_urls:
            rel_url = str(rel_url).strip()
            target = by_url.get(rel_url)
            if target and getattr(target, "file_name", ""):
                fn = target.file_name
                if fn and fn not in names:
                    names.append(fn)
        if names:
            doc.related_doc_names = names
