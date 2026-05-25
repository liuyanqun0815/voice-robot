#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(\|[^\]]+)?\]\]")
CODE_FENCE_RE = re.compile(r"(```[\s\S]*?```)")
BOLD_EMPTY_THEN_DESC = re.compile(r"^(\s*[-*]\s*)\*\*([：:])\s*(.+)$")


def normalize_key(name: str) -> str:
    s = name.strip().removesuffix(".md")
    s = s.replace(" ", "").replace("\u3000", "")
    s = re.sub(r"[-_]{2,}", "-", s)
    return s


def soft_key(name: str) -> str:
    s = normalize_key(name)
    s = re.sub(r"[^\w\u4e00-\u9fff\-（）()]+", "", s)
    return s


def build_alias_maps(stems: set[str]) -> tuple[dict[str, str], dict[str, str]]:
    exact_alias: dict[str, str] = {}
    soft_alias: dict[str, str] = {}
    for stem in stems:
        exact_alias[normalize_key(stem)] = stem
        soft_alias[soft_key(stem)] = stem
    return exact_alias, soft_alias


def find_replacement(
    target: str, stems: set[str], exact_alias: dict[str, str], soft_alias: dict[str, str]
) -> str | None:
    raw = target.strip().removesuffix(".md")
    if raw in stems:
        return raw

    nkey = normalize_key(raw)
    if nkey in exact_alias:
        return exact_alias[nkey]

    skey = soft_key(raw)
    if skey in soft_alias:
        return soft_alias[skey]

    # 对“问答-xxx[表情”这类截断名做前缀修复
    if raw.startswith(("问答-", "文档-", "概念-", "分类-")):
        prefix = normalize_key(raw)
        cands = [s for s in stems if normalize_key(s).startswith(prefix)]
        if len(cands) == 1:
            return cands[0]

    return None


def replace_links_in_text(
    text: str, stems: set[str], exact_alias: dict[str, str], soft_alias: dict[str, str]
) -> tuple[str, int]:
    changes = 0

    def _repl(match: re.Match[str]) -> str:
        nonlocal changes
        target = match.group(1)
        alias = match.group(2) or ""
        if target.startswith(".agent-queue/"):
            return match.group(0)
        replacement = find_replacement(target, stems, exact_alias, soft_alias)
        if replacement and replacement != target:
            changes += 1
            return f"[[{replacement}{alias}]]"
        return match.group(0)

    new_text = WIKILINK_RE.sub(_repl, text)
    return new_text, changes


def _wikilink_skip_target_for_strip(stem: str) -> bool:
    """与 kb_check 一致：不参与「是否存在对应文件」判断的目标，不删链接。"""
    if not stem or any(x in stem for x in ("${", '"', "/opt/", "http://", "https://")):
        return True
    return False


def _resolve_wiki_stem(
    target: str,
    stems: set[str],
    exact_alias: dict[str, str],
    soft_alias: dict[str, str],
) -> str | None:
    stem = target.strip().removesuffix(".md")
    if stem in stems:
        return stem
    return find_replacement(target, stems, exact_alias, soft_alias)


def _read_heading_title(path: Path) -> str:
    with path.open(encoding="utf-8", errors="ignore") as f:
        for _ in range(48):
            line = f.readline()
            if not line:
                break
            m = re.match(r"^#\s+(.+)$", line)
            if m:
                return m.group(1).strip().replace("\n", " ")[:120]
    return path.stem[:120]


def _cleanup_taxonomy_chunk(text: str) -> str:
    text = re.sub(r"\.md\)\]\]+\]+", ".md)", text)
    text = re.sub(r"\.md\]\]+\]+", ".md", text)
    text = re.sub(r"：\s*\]+\s*（", "：（", text)
    text = re.sub(r"\]\]+\s*（", "（", text)
    lines = text.split("\n")
    out: list[str] = []
    for line in lines:
        s = line.rstrip()
        m = BOLD_EMPTY_THEN_DESC.match(s)
        if m and m.group(3).strip():
            s = f"{m.group(1)}{m.group(3).lstrip()}"
        if re.match(r"^\s*[-*]\s*$", s):
            continue
        out.append(s)
    result = "\n".join(out)
    return re.sub(r"\n{4,}", "\n\n\n", result)


def normalize_taxonomy_wikilinks(
    text: str,
    stems: set[str],
    exact_alias: dict[str, str],
    soft_alias: dict[str, str],
    stem_to_rel: dict[str, str],
    stem_to_title: dict[str, str],
) -> tuple[str, int, int, int]:
    """分类/概念页：删真正死链；含 [] 的 stem 改为 Markdown 链接；其余规范为 [[stem]]。"""
    removed = 0
    to_markdown = 0
    canonicalized = 0

    def _repl(match: re.Match[str]) -> str:
        nonlocal removed, to_markdown, canonicalized
        target = match.group(1)
        alias = match.group(2) or ""
        display = alias[1:].strip() if alias.startswith("|") else ""

        if target.startswith(".agent-queue/"):
            return match.group(0)
        stem_quick = target.strip().removesuffix(".md")
        if _wikilink_skip_target_for_strip(stem_quick):
            return match.group(0)

        resolved = _resolve_wiki_stem(target, stems, exact_alias, soft_alias)
        if resolved is None:
            removed += 1
            return ""

        rel = stem_to_rel.get(resolved)
        if rel is None:
            removed += 1
            return ""

        if "[" in resolved or "]" in resolved:
            to_markdown += 1
            label = display or stem_to_title.get(resolved) or resolved
            label = re.sub(r"\s+", " ", label).strip()
            return f"[{label}]({rel})"

        raw_stem = target.strip().removesuffix(".md")
        if raw_stem != resolved or alias:
            canonicalized += 1
            return f"[[{resolved}{alias}]]"

        return match.group(0)

    def _norm_chunk(chunk: str) -> str:
        after = WIKILINK_RE.sub(_repl, chunk)
        return _cleanup_taxonomy_chunk(after)

    parts = CODE_FENCE_RE.split(text)
    rebuilt: list[str] = []
    for part in parts:
        if part.startswith("```"):
            rebuilt.append(part)
        else:
            rebuilt.append(_norm_chunk(part))
    return "".join(rebuilt), removed, to_markdown, canonicalized


def _is_taxonomy_markdown(path: Path) -> bool:
    ps = path.as_posix()
    return "/categories/" in ps or "/concepts/" in ps


def main() -> None:
    root = Path("e:/py_workspace/langchain-mcp/kefu-know/wiki")
    md_files = [p for p in root.rglob("*.md") if "/.agent-queue/" not in p.as_posix()]
    stems = {p.stem for p in md_files}
    exact_alias, soft_alias = build_alias_maps(stems)
    stem_to_rel = {p.stem: p.relative_to(root).as_posix().replace("\\", "/") for p in md_files}
    stem_to_title = {p.stem: _read_heading_title(p) for p in md_files}

    touched = 0
    total_rewrites = 0
    total_tax_removed = 0
    total_tax_md = 0
    total_tax_wiki = 0
    touched_files: list[str] = []
    for path in md_files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        new_text, rewrites = replace_links_in_text(text, stems, exact_alias, soft_alias)
        tax_rm = tax_md = tax_wiki = 0
        if _is_taxonomy_markdown(path):
            new_text, tax_rm, tax_md, tax_wiki = normalize_taxonomy_wikilinks(
                new_text,
                stems,
                exact_alias,
                soft_alias,
                stem_to_rel,
                stem_to_title,
            )
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            touched += 1
            total_rewrites += rewrites
            total_tax_removed += tax_rm
            total_tax_md += tax_md
            total_tax_wiki += tax_wiki
            touched_files.append(path.as_posix())

    report = {
        "files_scanned": len(md_files),
        "files_touched": touched,
        "link_rewrites": total_rewrites,
        "taxonomy_dead_wikilinks_removed": total_tax_removed,
        "taxonomy_wikilinks_to_markdown": total_tax_md,
        "taxonomy_wikilinks_canonicalized": total_tax_wiki,
        "sample_touched": touched_files[:30],
    }
    out = root / ".agent-queue" / "kb-autofix-report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
