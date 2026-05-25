#!/usr/bin/env python3
"""孤儿页修复：为无入链的 FAQ/概念/分类页增加来自主题页或 index 的引用。"""
from __future__ import annotations

import json
import re
from pathlib import Path

CATEGORY_FM = re.compile(r'^category:\s*"\[\[([^\]]+)\]\]"', re.M)
SECTION = "## 补链索引（孤儿页）"


def extract_category_stem(text: str) -> str | None:
    m = CATEGORY_FM.search(text)
    return m.group(1).strip().removesuffix(".md") if m else None


def has_wikilink_to(body: str, stem: str) -> bool:
    return bool(re.search(r"\[\[" + re.escape(stem) + r"(\||\]\])", body))


def ensure_section(body: str) -> str:
    if SECTION in body:
        return body
    body = body.rstrip() + "\n\n" + SECTION + "\n\n"
    return body


def append_link(body: str, stem: str) -> str:
    if "[" in stem or "]" in stem:
        return body
    if has_wikilink_to(body, stem):
        return body
    if SECTION not in body:
        body = ensure_section(body)
    idx = body.find(SECTION)
    rest = body[idx + len(SECTION) :]
    line = f"- [[{stem}]]\n"
    return body[: idx + len(SECTION)] + "\n\n" + line + rest.lstrip("\n")


def strip_code_blocks(text: str) -> str:
    return re.sub(r"```[\s\S]*?```", "", text)


def compute_incoming(md_files: list[Path]) -> dict[str, int]:
    incoming = {p.stem: 0 for p in md_files}
    for path in md_files:
        text = strip_code_blocks(path.read_text(encoding="utf-8", errors="ignore"))
        for link in re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", text):
            stem = link.strip().removesuffix(".md")
            if stem.startswith((".agent-queue/",)):
                continue
            if stem in incoming:
                incoming[stem] += 1
    return incoming


def main() -> None:
    root = Path("e:/py_workspace/langchain-mcp/kefu-know/wiki")
    index_path = root / "index.md"

    md_files = [
        p
        for p in root.rglob("*.md")
        if "/.agent-queue/" not in p.as_posix()
        and "/.archive/" not in p.as_posix()
        and "/.obsidian/" not in p.as_posix()
    ]
    incoming = compute_incoming(md_files)

    orphans: list[Path] = []
    for path in md_files:
        ps = path.as_posix()
        if not any(x in ps for x in ("/faqs/", "/concepts/", "/categories/")):
            continue
        if path.name in {"index.md", "overview.md", "log.md"}:
            continue
        if incoming.get(path.stem, 0) == 0:
            orphans.append(path)

    faq_by_cat: dict[str, list[str]] = {}
    concept_orphans: list[str] = []
    category_orphans: list[str] = []

    for path in orphans:
        stem = path.stem
        ps = path.as_posix()
        if "/categories/" in ps:
            category_orphans.append(stem)
        elif "/concepts/" in ps:
            concept_orphans.append(stem)
        else:
            text = path.read_text(encoding="utf-8", errors="ignore")
            cat = extract_category_stem(text)
            if cat:
                faq_by_cat.setdefault(cat, []).append(stem)

    cat_fixes = 0
    for cat_stem, faq_stems in faq_by_cat.items():
        cat_path = root / "categories" / f"{cat_stem}.md"
        if not cat_path.is_file():
            continue
        body = cat_path.read_text(encoding="utf-8", errors="ignore")
        orig = body
        for fs in sorted(set(faq_stems)):
            body = append_link(body, fs)
        if body != orig:
            cat_path.write_text(body, encoding="utf-8")
            cat_fixes += 1

    index_extra = ""
    index_body = index_path.read_text(encoding="utf-8", errors="ignore") if index_path.is_file() else ""

    if category_orphans:
        block = "\n### 其他主题页（补链索引）\n\n"
        for c in sorted(set(category_orphans)):
            if "[" in c or "]" in c:
                continue
            if not has_wikilink_to(index_body + index_extra, c):
                block += f"- [[{c}]]\n"
        if block.count("- ") > 0:
            index_extra += block

    if concept_orphans:
        block = "\n### 概念页补链（无入链别名）\n\n"
        for c in sorted(set(concept_orphans)):
            if "[" in c or "]" in c:
                continue
            if not has_wikilink_to(index_body + index_extra, c):
                block += f"- [[{c}]]\n"
        if block.count("- ") > 0:
            index_extra += block

    index_touched = False
    if index_extra.strip():
        index_path.write_text(index_body.rstrip() + "\n" + index_extra, encoding="utf-8")
        index_touched = True
        index_body = index_path.read_text(encoding="utf-8", errors="ignore")

    incoming2 = compute_incoming(md_files)
    orphans2: list[Path] = []
    for path in md_files:
        ps = path.as_posix()
        if not any(x in ps for x in ("/faqs/", "/concepts/", "/categories/")):
            continue
        if path.name in {"index.md", "overview.md", "log.md"}:
            continue
        if incoming2.get(path.stem, 0) == 0:
            orphans2.append(path)

    md_block_lines: list[str] = []
    for path in sorted(orphans2, key=lambda x: x.as_posix()):
        rel = path.relative_to(root).as_posix().replace("\\", "/")
        href = f"({rel})"
        if href in index_body:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"^# (.+)$", text, re.M)
        title = (m.group(1).strip() if m else path.stem).replace("\n", " ")[:120]
        md_block_lines.append(f"- [{title}]({rel})")

    md_touched = False
    if md_block_lines:
        block = "\n### 标准 Markdown 补链（wikilink 不适用的页面）\n\n" + "\n".join(md_block_lines) + "\n"
        index_path.write_text(index_body.rstrip() + block, encoding="utf-8")
        md_touched = True
        index_touched = index_touched or md_touched

    faq_linked = sum(len(v) for v in faq_by_cat.values())
    report = {
        "orphans_total": len(orphans),
        "faq_orphans_linked_to_category": faq_linked,
        "category_files_updated": cat_fixes,
        "concept_orphans_in_index": len(set(concept_orphans)),
        "category_orphans_in_index": len(set(category_orphans)),
        "index_updated": index_touched,
        "markdown_fallback_links_added": len(md_block_lines),
        "orphans_after_wikilink_pass": len(orphans2),
        "faq_orphan_stems_unresolved": [
            s.stem
            for s in orphans
            if "/faqs/" in s.as_posix()
            and extract_category_stem(s.read_text(encoding="utf-8", errors="ignore")) is None
        ][:20],
    }

    out = root / ".agent-queue" / "kb-orphanfix-report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
