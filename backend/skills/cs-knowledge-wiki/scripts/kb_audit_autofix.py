#!/usr/bin/env python3
"""自动修复知识库审计问题（优先修复可安全自动化项）。"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
from pathlib import Path

WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
SECTION_CATEGORIES = "## 主题分类"
SECTION_CONCEPTS = "## 关键概念"


def configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            return
        except (OSError, ValueError):
            pass
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="自动修复知识库审计问题")
    parser.add_argument("--root", type=Path, default=Path("e:/py_workspace/langchain-mcp/kefu-know/wiki"))
    parser.add_argument("--dry-run", action="store_true", help="仅输出计划，不写入文件")
    parser.add_argument("--skip-link-fix", action="store_true", help="跳过 kb_autofix_links.py")
    parser.add_argument("--skip-orphan-fix", action="store_true", help="跳过 kb_fix_orphans.py")
    parser.add_argument("--remove-sessions-dir", action="store_true", help="若存在 sessions 目录则删除（危险）")
    return parser.parse_args()


def run_script(script_path: Path, dry_run: bool) -> dict:
    if dry_run:
        return {"script": script_path.name, "status": "planned"}
    result = subprocess.run(
        [sys.executable, str(script_path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "script": script_path.name,
        "status": "ok" if result.returncode == 0 else "failed",
        "return_code": result.returncode,
        "stdout_tail": result.stdout[-1200:],
        "stderr_tail": result.stderr[-1200:],
    }


def extract_index_targets(index_text: str) -> set[str]:
    targets = set()
    for stem in WIKILINK_RE.findall(index_text):
        key = stem.strip().removesuffix(".md")
        if key:
            targets.add(key)
    return targets


def ensure_section(text: str, section: str) -> str:
    if section in text:
        return text
    return text.rstrip() + f"\n\n{section}\n\n"


def append_missing_links_to_index(
    root: Path,
    dry_run: bool,
) -> dict:
    index_path = root / "index.md"
    if not index_path.is_file():
        return {"index_updated": False, "reason": "index.md 不存在", "added": {"categories": 0, "concepts": 0}}

    category_files = sorted((root / "categories").glob("*.md"))
    concept_files = sorted((root / "concepts").glob("*.md"))

    index_text = index_path.read_text(encoding="utf-8", errors="ignore")
    original = index_text
    index_targets = extract_index_targets(index_text)

    missing_categories = [p.stem for p in category_files if p.stem not in index_targets]
    missing_concepts = [p.stem for p in concept_files if p.stem not in index_targets]

    added_cat = 0
    added_con = 0

    if missing_categories:
        index_text = ensure_section(index_text, SECTION_CATEGORIES)
        block = "\n### 自动补链主题\n\n"
        for stem in missing_categories:
            block += f"- [[{stem}]]\n"
            added_cat += 1
        index_text = index_text.rstrip() + "\n" + block

    if missing_concepts:
        index_text = ensure_section(index_text, SECTION_CONCEPTS)
        block = "\n### 自动补链概念\n\n"
        for stem in missing_concepts:
            block += f"- [[{stem}]]\n"
            added_con += 1
        index_text = index_text.rstrip() + "\n" + block

    changed = index_text != original
    if changed and (not dry_run):
        index_path.write_text(index_text, encoding="utf-8")

    return {
        "index_updated": changed and (not dry_run),
        "planned_index_update": changed and dry_run,
        "added": {"categories": added_cat, "concepts": added_con},
        "missing_categories_sample": missing_categories[:30],
        "missing_concepts_sample": missing_concepts[:30],
    }


def fix_concept_backlinks(root: Path, dry_run: bool) -> dict:
    """为无 FAQ 回链的概念页补一个兜底 FAQ 列表块。"""
    concept_files = sorted((root / "concepts").glob("*.md"))
    faq_files = sorted((root / "faqs").glob("*.md"))
    faq_stems = [p.stem for p in faq_files]

    touched = 0
    patched: list[str] = []

    for concept in concept_files:
        text = concept.read_text(encoding="utf-8", errors="ignore")
        targets = {s.strip().removesuffix(".md") for s in WIKILINK_RE.findall(text)}
        has_faq = any(t in faq_stems for t in targets)
        if has_faq:
            continue

        # 基于概念标题挑选 FAQ（弱匹配，避免空回链）
        title_match = re.search(r"^#\s+(.+)$", text, re.M)
        title = title_match.group(1).strip() if title_match else concept.stem
        tokens = [x for x in re.split(r"[\s\-（）()、，,]+", title) if len(x) >= 2]

        picked: list[str] = []
        for stem in faq_stems:
            if any(tok.lower() in stem.lower() for tok in tokens):
                picked.append(stem)
            if len(picked) >= 5:
                break

        if not picked:
            continue

        block = "\n## 自动补链 FAQ\n\n" + "\n".join(f"- [[{s}]]" for s in picked) + "\n"
        if "## 自动补链 FAQ" in text:
            continue
        new_text = text.rstrip() + block
        if not dry_run:
            concept.write_text(new_text, encoding="utf-8")
        touched += 1
        patched.append(concept.as_posix())

    return {
        "concept_files_updated": touched if not dry_run else 0,
        "planned_updates": touched if dry_run else 0,
        "sample": patched[:20],
    }


def remove_sessions_dir(root: Path, dry_run: bool) -> dict:
    sessions_dir = root / "sessions"
    if not sessions_dir.exists():
        return {"removed": False, "exists_before": False}
    if dry_run:
        return {"removed": False, "planned_remove": True, "exists_before": True}
    # 仅删除空目录；非空目录保持不动，避免危险删除
    try:
        sessions_dir.rmdir()
        return {"removed": True, "exists_before": True, "reason": "empty_dir_removed"}
    except OSError:
        return {"removed": False, "exists_before": True, "reason": "non_empty_dir_skip"}


def main() -> None:
    configure_stdout()
    args = parse_args()
    root = args.root

    if not root.exists():
        print(json.dumps({"error": f"路径不存在: {root.as_posix()}"}, ensure_ascii=False))
        return

    script_dir = Path(__file__).resolve().parent
    report: dict = {
        "root": root.as_posix(),
        "dry_run": args.dry_run,
        "steps": {},
    }

    if not args.skip_link_fix:
        report["steps"]["kb_autofix_links"] = run_script(script_dir / "kb_autofix_links.py", dry_run=args.dry_run)
    if not args.skip_orphan_fix:
        report["steps"]["kb_fix_orphans"] = run_script(script_dir / "kb_fix_orphans.py", dry_run=args.dry_run)

    report["steps"]["index_consistency_fix"] = append_missing_links_to_index(root, dry_run=args.dry_run)
    report["steps"]["concept_backlink_fix"] = fix_concept_backlinks(root, dry_run=args.dry_run)

    if args.remove_sessions_dir:
        report["steps"]["sessions_dir_cleanup"] = remove_sessions_dir(root, dry_run=args.dry_run)
    else:
        report["steps"]["sessions_dir_cleanup"] = {"skipped": True, "reason": "not_enabled"}

    out = root / ".agent-queue" / "kb-audit-autofix-report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "output": out.as_posix(), "dry_run": args.dry_run}, ensure_ascii=False))


if __name__ == "__main__":
    main()
