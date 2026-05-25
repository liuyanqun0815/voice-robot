#!/usr/bin/env python3
"""从 raw/websites 构建分层知识库：LLM 动态生成分类与概念，支持多目录合并。"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from kb_utils import clean_page_title, normalize_text, repair_mojibake, sanitize_filename
from agent_ingest import (
    apply_doc_enrichments,
    apply_taxonomy_assignments,
    export_taxonomy_request,
    load_taxonomy_file,
)
from wiki_taxonomy_llm import (
    CategoryDef,
    ConceptDef,
    enrich_concept_points,
    heuristic_taxonomy,
    save_taxonomy_json,
)


@dataclass
class DocItem:
    title: str
    content: str
    source_url: str
    category_id: str = ""
    core_name: str = ""
    source_site: str = ""
    summary: str = ""
    file_name: str = ""
    related_doc_names: list[str] = field(default_factory=list)


@dataclass
class ConceptItem:
    concept_id: str
    title: str
    definition: str
    file_name: str = ""
    points: list[str] = field(default_factory=list)
    related_doc_names: list[str] = field(default_factory=list)
    category_ids: list[str] = field(default_factory=list)


def collect_website_files(websites_dirs: list[Path], websites_root: Path | None) -> list[Path]:
    files: list[Path] = []
    for d in websites_dirs:
        if d.is_dir():
            files.extend(sorted(d.glob("*.md")))
    if websites_root and websites_root.is_dir():
        for sub in sorted(websites_root.iterdir()):
            if sub.is_dir():
                files.extend(sorted(sub.glob("*.md")))
    by_url: dict[str, Path] = {}
    for path in files:
        try:
            data = parse_website_file(path)
            url = data.get("source_url") or str(path)
            prev = by_url.get(url)
            if not prev or path.stat().st_size > prev.stat().st_size:
                by_url[url] = path
        except Exception:
            by_url[str(path)] = path
    return sorted(by_url.values(), key=lambda p: p.name)


def parse_website_file(path: Path) -> dict:
    content = repair_mojibake(path.read_text(encoding="utf-8"))
    frontmatter: dict[str, str] = {}
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            fm_text = content[3:end].strip()
            for line in fm_text.split("\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    frontmatter[k.strip()] = normalize_text(v.strip())
            content = content[end + 3 :].strip()

    title = clean_page_title(frontmatter.get("title", ""))
    if not title:
        title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        if title_match:
            title = clean_page_title(title_match.group(1))

    return {
        "title": title,
        "content": normalize_text(content),
        "source_url": frontmatter.get("source_url", ""),
        "fetched_at": frontmatter.get("fetched_at", ""),
        "core_name": frontmatter.get("filename_core") or path.stem,
        "source_site": path.parent.name,
        "path": path,
    }


def extract_summary(content: str, max_len: int = 300) -> str:
    text = re.sub(r"[#*`\[\]()\-_]", " ", content)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len] + "..." if len(text) > max_len else text


def generate_doc_name(title: str, core_name: str, used: set[str]) -> str:
    base = sanitize_filename(core_name or title)
    name = f"文档-{base}" if base and not base.startswith("文档-") else base or "文档-未命名"
    if name not in used:
        used.add(name)
        return name
    suffix = hashlib.md5((title + core_name).encode()).hexdigest()[:6]
    candidate = f"{name}-{suffix}"
    n = 2
    while candidate in used:
        candidate = f"{name}-{suffix}{n}"
        n += 1
    used.add(candidate)
    return candidate


def category_file_name(cat: CategoryDef, used: set[str]) -> str:
    short = re.sub(r"[（(]([^）)]+)[）)]", r"-\1", cat.title)
    short = re.sub(r"[/\s]+", "-", short)
    short = re.sub(r'[\\/:*?"<>|\r\n]', "", short)
    name = f"分类-{short}"
    if name not in used:
        used.add(name)
        return name
    suffix = hashlib.md5(cat.id.encode()).hexdigest()[:6]
    candidate = f"{name}-{suffix}"
    used.add(candidate)
    return candidate


def concept_file_name(title: str, concept_id: str, used: set[str]) -> str:
    short = re.sub(r'[\\/:*?"<>|\r\n]', "", title)
    name = f"概念-{short}"
    if name not in used:
        used.add(name)
        return name
    suffix = hashlib.md5(concept_id.encode()).hexdigest()[:6]
    candidate = f"{name}-{suffix}"
    used.add(candidate)
    return candidate


def concepts_to_items(
    concept_defs: list[ConceptDef],
    docs: list[DocItem],
    used: set[str],
) -> dict[str, ConceptItem]:
    items: dict[str, ConceptItem] = {}
    for c in concept_defs:
        related_names: list[str] = []
        cat_ids: set[str] = set()
        for idx in c.related_doc_indices:
            if 0 <= idx < len(docs):
                if docs[idx].file_name:
                    related_names.append(docs[idx].file_name)
                cat_ids.add(docs[idx].category_id)
        item = ConceptItem(
            concept_id=c.id,
            title=c.title,
            definition=c.definition,
            points=c.points,
            related_doc_names=list(dict.fromkeys(related_names))[:20],
            category_ids=sorted(cat_ids),
        )
        item.file_name = concept_file_name(item.title, item.concept_id, used)
        items[c.id] = item
    return items


def render_doc_file(doc: DocItem, category_link: str, cat_title: str, today: str) -> str:
    body = f"""---
type: website-doc
category: "[[{category_link}]]"
tags: [{cat_title}]
source_url: {doc.source_url}
source_site: {doc.source_site}
updated_at: {today}
---

# {doc.title}

## 内容摘要

{doc.summary}

## 正文内容

{doc.content}

## 相关
- [[{category_link}]]
- 来源：{doc.source_url}
"""
    for name in doc.related_doc_names[:15]:
        body += f"- [[{name}]]\n"
    return body


def render_category_file(
    cat: CategoryDef,
    category_link: str,
    docs: list[DocItem],
    concepts: list[ConceptItem],
    today: str,
) -> str:
    docs_sorted = sorted(docs, key=lambda x: len(x.content), reverse=True)
    body = f"""---
type: category
category_id: {cat.id}
doc_count: {len(docs_sorted)}
updated_at: {today}
taxonomy: llm
---

# {cat.title}

## 主题概述

{cat.overview}

本主题共整理 **{len(docs_sorted)}** 篇文档。

"""
    cat_concepts = [c for c in concepts if cat.id in c.category_ids]
    if cat_concepts:
        body += "## 相关产品概念\n\n"
        for c in cat_concepts:
            body += f"- **[[{c.file_name}]]**：{c.definition}\n"
        body += "\n"

    body += "## 文档列表\n\n"
    for idx, doc in enumerate(docs_sorted, 1):
        body += f"### {idx}. {doc.title}\n\n"
        body += f"> 详情页：[[{doc.file_name}]]\n\n"
        body += f"**摘要：** {doc.summary}\n\n"
        body += f"**来源：** {doc.source_url}\n\n"
        body += "---\n\n"
    return body


def render_concept(c: ConceptItem, cat_name_map: dict[str, str], today: str) -> str:
    body = f"""---
type: concept
concept_id: {c.concept_id}
updated_at: {today}
taxonomy: llm
---

# {c.title}

## 定义

{c.definition}

## 要点

"""
    for p in c.points:
        body += f"- {p}\n"
    if not c.points:
        body += "- （见下方关联文档）\n"
    body += "\n## 关联文档\n"
    for name in c.related_doc_names[:25]:
        body += f"- [[{name}]]\n"
    body += "\n## 相关主题\n"
    for cid in c.category_ids:
        link = cat_name_map.get(cid, f"分类-{cid}")
        body += f"- [[{link}]]\n"
    return body


def render_index(
    categories: dict[str, CategoryDef],
    doc_by_cat: dict[str, list[DocItem]],
    concepts: dict[str, ConceptItem],
    cat_name_map: dict[str, str],
    stats: dict,
    today: str,
    mode: str,
) -> str:
    lines = [
        "# SCNet 知识库",
        "",
        f"> 网站文档萃取（分类/概念由 **{mode}** 生成）。更新：{today}",
        "",
        "## 使用说明",
        "",
        "1. 从 **主题分类** 进入各主题汇总页",
        "2. 单篇文档见 `wiki/faqs/`",
        "3. 概念见 `wiki/concepts/`",
        "4. LLM 生成的分类树见 `wiki/taxonomy.json`",
        "",
        "## 统计",
        "",
        f"- 主题分类：**{stats['categories']}** 个（动态生成）",
        f"- 文档数量：**{stats['docs']}** 篇",
        f"- 产品概念：**{stats['concepts']}** 个（动态生成）",
        f"- 原始页面：**{stats['raw_pages']}** 页",
        f"- 来源站点：{', '.join(stats.get('sites', []))}",
        "",
        "## 主题分类",
        "",
    ]
    for cat_id, cat in categories.items():
        docs = doc_by_cat.get(cat_id, [])
        if not docs:
            continue
        lines.append(f"### {cat.title}")
        lines.append("")
        lines.append(cat.overview)
        lines.append("")
        lines.append(f"- 主题页：[[{cat_name_map[cat_id]}]]")
        lines.append(f"- 本主题文档 **{len(docs)}** 篇")
        top = sorted(docs, key=lambda x: len(x.content), reverse=True)[:5]
        if top:
            lines.append("- 代表文档：")
            for d in top:
                lines.append(f"  - [[{d.file_name}]] {d.title}")
        lines.append("")
    if concepts:
        lines.append("## 产品概念\n")
        for c in concepts.values():
            lines.append(f"- [[{c.file_name}]]：{c.title}")
    return "\n".join(lines)


def render_overview(
    categories: dict[str, CategoryDef],
    doc_by_cat: dict[str, list[DocItem]],
    concepts: dict[str, ConceptItem],
    cat_name_map: dict[str, str],
    stats: dict,
    today: str,
) -> str:
    lines = [
        "# 知识库总览",
        "",
        f"> 更新：{today}",
        "",
        "## 知识结构",
        "",
        "| 层级 | 目录 | 说明 |",
        "|------|------|------|",
        "| 索引 | `index.md` | 主题入口 |",
        "| 分类树 | `taxonomy.json` | LLM 生成的分类与概念定义 |",
        "| 主题 | `categories/` | 各主题文档汇总 |",
        "| 文档 | `faqs/` | 单篇萃取文档 |",
        "| 概念 | `concepts/` | 术语与产品概念 |",
        "",
        f"- 文档：**{stats['docs']}** 篇",
        f"- 概念：**{stats['concepts']}** 个",
        "",
        "## 主题分布",
        "",
    ]
    for cat_id, cat in categories.items():
        n = len(doc_by_cat.get(cat_id, []))
        if n:
            lines.append(f"- **{cat.title}**：{n} 篇 → [[{cat_name_map[cat_id]}]]")
    return "\n".join(lines)


def clear_wiki(wiki_dir: Path) -> None:
    for sub in ("faqs", "concepts", "categories"):
        d = wiki_dir / sub
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)


def ensure_wiki_dirs(wiki_dir: Path) -> None:
    for sub in ("faqs", "concepts", "categories"):
        (wiki_dir / sub).mkdir(parents=True, exist_ok=True)


def parse_doc_markdown(path: Path) -> DocItem | None:
    text = path.read_text(encoding="utf-8")
    if "type: website-doc" not in text[:400]:
        return None
    m_url = re.search(r"^source_url:\s*(.+)$", text, re.M)
    m_site = re.search(r"^source_site:\s*(.+)$", text, re.M)
    m_title = re.search(r"^# (.+)$", text, re.M)
    if not m_title:
        return None
    title = m_title.group(1).strip()
    m_body = re.search(r"## 正文内容\s*\n+([\s\S]*?)(?=\n## |\Z)", text)
    content = m_body.group(1).strip() if m_body else ""
    m_sum = re.search(r"## 内容摘要\s*\n+([\s\S]*?)(?=\n## |\Z)", text)
    summary = m_sum.group(1).strip() if m_sum else extract_summary(content)
    return DocItem(
        title=title,
        content=content,
        source_url=m_url.group(1).strip() if m_url else "",
        source_site=m_site.group(1).strip() if m_site else "",
        summary=summary,
        file_name=path.stem,
    )


def load_existing_website_docs(faqs_dir: Path) -> dict[str, DocItem]:
    if not faqs_dir.is_dir():
        return {}
    by_url: dict[str, DocItem] = {}
    for path in faqs_dir.glob("*.md"):
        doc = parse_doc_markdown(path)
        if doc and doc.source_url:
            by_url[doc.source_url] = doc
    return by_url


def load_existing_concepts(wiki_dir: Path) -> dict[str, ConceptItem]:
    concepts_dir = wiki_dir / "concepts"
    if not concepts_dir.is_dir():
        return {}
    out: dict[str, ConceptItem] = {}
    for path in concepts_dir.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        m_id = re.search(r"^concept_id:\s*(\S+)", text, re.M)
        if not m_id:
            continue
        cid = m_id.group(1).strip()
        m_title = re.search(r"^# (.+)$", text, re.M)
        title = m_title.group(1).strip() if m_title else cid
        m_def = re.search(r"## 定义\s*\n+([\s\S]*?)(?=\n## |\Z)", text)
        definition = m_def.group(1).strip() if m_def else title
        out[cid] = ConceptItem(
            concept_id=cid,
            title=title,
            definition=definition,
            file_name=path.stem,
        )
    return out


def merge_concept_items(
    existing: dict[str, ConceptItem],
    incoming: dict[str, ConceptItem],
) -> dict[str, ConceptItem]:
    out = dict(existing)
    for cid, new_c in incoming.items():
        if cid not in out:
            out[cid] = new_c
            continue
        old = out[cid]
        out[cid] = ConceptItem(
            concept_id=cid,
            title=new_c.title or old.title,
            definition=new_c.definition or old.definition,
            file_name=old.file_name or new_c.file_name,
            points=new_c.points or old.points,
            related_doc_names=list(dict.fromkeys(old.related_doc_names + new_c.related_doc_names)),
            category_ids=list(dict.fromkeys(old.category_ids + new_c.category_ids)),
        )
    return out


def load_docs_from_files(files: list[Path]) -> tuple[list[DocItem], int]:
    docs: list[DocItem] = []
    skipped = 0
    for path in files:
        try:
            data = parse_website_file(path)
            if not data["title"] or len(data["content"]) < 80:
                skipped += 1
                continue
            docs.append(
                DocItem(
                    title=data["title"],
                    content=data["content"],
                    source_url=data["source_url"],
                    core_name=data["core_name"],
                    source_site=data["source_site"],
                    summary=extract_summary(data["content"]),
                )
            )
        except Exception as exc:
            print(f"Error {path.name}: {exc}")
            skipped += 1
    return docs, skipped


def write_wiki(
    wiki_dir: Path,
    docs: list[DocItem],
    categories: dict[str, CategoryDef],
    concept_defs: list[ConceptDef],
    source_label: str,
    raw_page_count: int,
    skipped: int,
    mode: str,
    model: str,
    existing_concepts: dict[str, ConceptItem] | None = None,
) -> dict:
    doc_by_cat: dict[str, list[DocItem]] = defaultdict(list)
    for doc in docs:
        doc_by_cat[doc.category_id].append(doc)

    today = date.today().isoformat()
    used: set[str] = set()
    cat_name_map: dict[str, str] = {}
    for cat_id, cat in categories.items():
        if doc_by_cat.get(cat_id):
            cat_name_map[cat_id] = category_file_name(cat, used)

    for doc in docs:
        if doc.file_name:
            used.add(doc.file_name)
        else:
            doc.file_name = generate_doc_name(doc.title, doc.core_name, used)

    new_concepts = concepts_to_items(concept_defs, docs, used)
    concepts = new_concepts if not existing_concepts else merge_concept_items(existing_concepts, new_concepts)
    for c in concepts.values():
        if not c.file_name:
            c.file_name = concept_file_name(c.title, c.concept_id, used)
        else:
            used.add(c.file_name)

    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "faqs").mkdir(exist_ok=True)
    (wiki_dir / "concepts").mkdir(exist_ok=True)
    (wiki_dir / "categories").mkdir(exist_ok=True)

    save_taxonomy_json(wiki_dir / "taxonomy.json", categories, concept_defs, model)

    for doc in docs:
        cat = categories.get(doc.category_id)
        cat_title = cat.title if cat else doc.category_id
        cat_link = cat_name_map.get(doc.category_id, "分类-general")
        (wiki_dir / "faqs" / f"{doc.file_name}.md").write_text(
            render_doc_file(doc, cat_link, cat_title, today), encoding="utf-8"
        )

    for c in concepts.values():
        (wiki_dir / "concepts" / f"{c.file_name}.md").write_text(
            render_concept(c, cat_name_map, today), encoding="utf-8"
        )

    for cat_id, cat in categories.items():
        cat_docs = doc_by_cat.get(cat_id, [])
        if not cat_docs:
            continue
        link = cat_name_map[cat_id]
        (wiki_dir / "categories" / f"{link}.md").write_text(
            render_category_file(cat, link, cat_docs, list(concepts.values()), today),
            encoding="utf-8",
        )

    sites = sorted({d.source_site for d in docs if d.source_site})
    stats = {
        "docs": len(docs),
        "concepts": len(concepts),
        "categories": len(cat_name_map),
        "raw_pages": raw_page_count,
        "sites": sites,
        "taxonomy_mode": mode,
    }

    (wiki_dir / "index.md").write_text(
        render_index(categories, doc_by_cat, concepts, cat_name_map, stats, today, mode),
        encoding="utf-8",
    )
    (wiki_dir / "overview.md").write_text(
        render_overview(categories, doc_by_cat, concepts, cat_name_map, stats, today),
        encoding="utf-8",
    )

    log = wiki_dir / "log.md"
    entry = (
        f"\n## {today} 网站萃取 ({source_label}, mode={mode})\n"
        f"- 文档: {stats['docs']}，概念: {stats['concepts']}，分类: {stats['categories']}，跳过: {skipped}\n"
        f"- 策略：同 source_url 更新、新页新增，**不删除** wiki 已有文档\n"
        f"- taxonomy: `wiki/taxonomy.json`（LLM 动态生成）\n"
        f"- 来源站点: {', '.join(sites) or source_label}\n"
    )
    log.write_text((log.read_text(encoding="utf-8") if log.exists() else "# 操作日志\n") + entry, encoding="utf-8")
    return stats


def _format_doc_line(index: int, doc: DocItem) -> str:
    return f"{index}. [{doc.source_site}] {doc.title}\n   {doc.summary[:220]}"


def main() -> None:
    parser = argparse.ArgumentParser(description="将 raw/websites 萃取到 wiki（分类由 Agent 生成 taxonomy.json）")
    parser.add_argument("--websites-dir", type=Path, action="append", default=[])
    parser.add_argument("--websites-root", type=Path, default=None)
    parser.add_argument("--wiki-dir", type=Path, required=True)
    parser.add_argument("--source-name", default="website-merge")
    parser.add_argument(
        "--mode",
        choices=("agent", "heuristic"),
        default="agent",
        help="agent=使用 Agent 生成的 wiki/taxonomy.json；heuristic=按来源站点粗分",
    )
    parser.add_argument(
        "--taxonomy",
        type=Path,
        default=None,
        help="Agent 生成的 taxonomy.json（含 categories、concepts、assignments）",
    )
    parser.add_argument(
        "--export-taxonomy-queue",
        type=Path,
        default=None,
        help="仅导出文档清单供 Agent 生成 taxonomy，不写入 wiki",
    )
    parser.add_argument(
        "--full-rebuild",
        action="store_true",
        help="清空 wiki/faqs、concepts、categories 后全量重建（默认增量：同 URL 更新、新页新增、不删已有）",
    )
    args = parser.parse_args()

    websites_root = args.websites_root
    if not args.websites_dir and not websites_root:
        default_root = Path("kefu-know/raw/websites")
        if default_root.is_dir():
            websites_root = default_root

    files = collect_website_files(args.websites_dir, websites_root)
    print(f"Found {len(files)} website pages (deduped)")

    if args.full_rebuild:
        clear_wiki(args.wiki_dir)
        docs_by_url: dict[str, DocItem] = {}
    else:
        ensure_wiki_dirs(args.wiki_dir)
        docs_by_url = load_existing_website_docs(args.wiki_dir / "faqs")

    incoming, skipped = load_docs_from_files(files)
    if not incoming and not docs_by_url:
        raise SystemExit("无可用文档，请先抓取网站到 raw/websites/")

    if args.export_taxonomy_queue:
        n = export_taxonomy_request(incoming, args.export_taxonomy_queue, _format_doc_line)
        print(f"Exported taxonomy request for {n} docs -> {args.export_taxonomy_queue}")
        print("由上层 Agent 生成 wiki/taxonomy.json 后，再运行本脚本 --taxonomy wiki/taxonomy.json")
        return

    used_names = {p.stem for p in (args.wiki_dir / "faqs").glob("*.md")} if (args.wiki_dir / "faqs").is_dir() else set()
    for doc in incoming:
        if doc.source_url and doc.source_url in docs_by_url:
            doc.file_name = docs_by_url[doc.source_url].file_name
        else:
            doc.file_name = generate_doc_name(doc.title, doc.core_name, used_names)
        key = doc.source_url or f"file://{doc.file_name}"
        docs_by_url[key] = doc

    docs = list(docs_by_url.values())

    taxonomy_path = args.taxonomy or (args.wiki_dir / "taxonomy.json")
    if args.mode == "agent":
        if not taxonomy_path.is_file():
            raise SystemExit(
                f"agent 模式需要 taxonomy 文件: {taxonomy_path}\n"
                "请先运行 --export-taxonomy-queue，由 Agent 写入 taxonomy.json"
            )
        categories, concept_defs, by_url, by_index, taxonomy_data = load_taxonomy_file(taxonomy_path)
        apply_taxonomy_assignments(docs, by_url, by_index, categories)
        apply_doc_enrichments(docs, taxonomy_data)
        enrich_concept_points(concept_defs, docs, categories)
        taxonomy_source = "cursor-agent"
    else:
        categories, concept_defs, _, _ = heuristic_taxonomy(docs)
        taxonomy_source = "heuristic"

    existing_concepts = {} if args.full_rebuild else load_existing_concepts(args.wiki_dir)
    stats = write_wiki(
        args.wiki_dir,
        docs,
        categories,
        concept_defs,
        args.source_name,
        len(files),
        skipped,
        args.mode,
        taxonomy_source,
        existing_concepts=existing_concepts,
    )
    mode_label = "全量重建" if args.full_rebuild else "增量更新"
    print(
        f"Done ({mode_label}). docs={stats['docs']} concepts={stats['concepts']} "
        f"categories={stats['categories']} mode={args.mode} skipped={skipped}"
    )


if __name__ == "__main__":
    main()
