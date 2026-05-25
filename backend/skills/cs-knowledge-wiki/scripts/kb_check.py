#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import sys
import re
from pathlib import Path

MD_REL_LINK_RE = re.compile(r"\]\(([^)]+)\)")


def strip_code_blocks(text: str) -> str:
    return re.sub(r"```[\s\S]*?```", "", text)


def collect_broken_wikilinks(md_files: list[Path], all_stems: set[str]) -> list[tuple[str, str]]:
    broken: list[tuple[str, str]] = []
    for path in md_files:
        text = strip_code_blocks(path.read_text(encoding="utf-8", errors="ignore"))
        for link in re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", text):
            stem = link.strip().removesuffix(".md")
            if stem.startswith((".agent-queue/",)):
                continue
            if not stem or any(x in stem for x in ("${", '"', "/opt/", "http://", "https://")):
                continue
            if stem not in all_stems:
                broken.append((path.as_posix(), stem))
    return broken


def count_incoming_links(
    md_files: list[Path],
    include_markdown_links: bool,
) -> dict[str, int]:
    incoming = {p.stem: 0 for p in md_files}
    for path in md_files:
        text = strip_code_blocks(path.read_text(encoding="utf-8", errors="ignore"))
        for link in re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", text):
            stem = link.strip().removesuffix(".md")
            if stem.startswith((".agent-queue/",)):
                continue
            if not stem or any(x in stem for x in ("${", '"', "/opt/", "http://", "https://")):
                continue
            if stem in incoming:
                incoming[stem] += 1
        if include_markdown_links:
            for href in MD_REL_LINK_RE.findall(text):
                href = href.strip().split("#")[0].strip()
                if href.startswith("http"):
                    continue
                href = href.replace("\\", "/").lstrip("./")
                if not href.endswith(".md"):
                    continue
                if not (
                    href.startswith("faqs/")
                    or href.startswith("concepts/")
                    or href.startswith("categories/")
                ):
                    continue
                name = href.split("/")[-1]
                stem = name.removesuffix(".md")
                if stem in incoming:
                    incoming[stem] += 1
    return incoming


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    root = Path("e:/py_workspace/langchain-mcp/kefu-know/wiki")
    md_files = list(root.rglob("*.md"))
    all_stems = {p.stem for p in md_files}

    counts = {"faqs": 0, "categories": 0, "concepts": 0, "drafts": 0}
    for path in md_files:
        ps = path.as_posix()
        if "/faqs/" in ps:
            counts["faqs"] += 1
        if "/categories/" in ps:
            counts["categories"] += 1
        if "/concepts/" in ps:
            counts["concepts"] += 1
        if "/.agent-queue/faq-drafts/" in ps:
            counts["drafts"] += 1

    broken = collect_broken_wikilinks(md_files, all_stems)
    incoming_wiki = count_incoming_links(md_files, include_markdown_links=False)
    incoming_full = count_incoming_links(md_files, include_markdown_links=True)

    orphans_wiki: list[str] = []
    orphans_full: list[str] = []
    for path in md_files:
        ps = path.as_posix()
        if not any(x in ps for x in ("/faqs/", "/concepts/", "/categories/")):
            continue
        if path.name in {"index.md", "overview.md", "log.md"}:
            continue
        if incoming_wiki.get(path.stem, 0) == 0:
            orphans_wiki.append(ps)
        if incoming_full.get(path.stem, 0) == 0:
            orphans_full.append(ps)

    summary = {
        "total_md": len(md_files),
        "counts": counts,
        "broken_links": len(broken),
        "orphan_pages_wikilink_only": len(orphans_wiki),
        "orphan_pages_including_md_links": len(orphans_full),
        "sample_broken": broken[:15],
        "sample_orphans_wiki": orphans_wiki[:15],
        "sample_orphans_full": orphans_full[:15],
    }

    out = root / ".agent-queue" / "kb-check-report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
