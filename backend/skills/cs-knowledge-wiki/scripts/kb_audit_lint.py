#!/usr/bin/env python3
"""执行知识库 6 项 lint 检查（支持交互输入与命令行模式）。"""

from __future__ import annotations

import argparse
import io
import itertools
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
MD_REL_LINK_RE = re.compile(r"\]\(([^)]+)\)")
HEADING_CATEGORY_SUMMARY = "## 主题内问答总结"


@dataclass
class AuditContext:
    root: Path
    md_files: list[Path]
    stems: set[str]
    stem_to_path: dict[str, Path]
    index_path: Path
    category_files: list[Path]
    concept_files: list[Path]
    faq_files: list[Path]


def configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            return
        except (OSError, ValueError):
            pass
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def strip_code_blocks(text: str) -> str:
    return re.sub(r"```[\s\S]*?```", "", text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检查知识库 7 项 lint")
    parser.add_argument("--root", type=Path, default=None, help="wiki 根目录，默认交互输入")
    parser.add_argument("--output", type=Path, default=None, help="输出报告路径（.json）")
    parser.add_argument("--interactive", action="store_true", help="强制进入交互模式")
    parser.add_argument("--threshold", type=float, default=0.92, help="FAQ 相似判定阈值（默认 0.92）")
    parser.add_argument(
        "--link-threshold",
        type=float,
        default=0.95,
        help="分类/概念页链接高相似判定阈值（默认 0.95）",
    )
    return parser.parse_args()


def interactive_resolve_root(default_root: Path) -> Path:
    print("请输入 wiki 根目录（直接回车使用默认）：")
    print(f"默认: {default_root.as_posix()}")
    user_input = input("> ").strip()
    if not user_input:
        return default_root
    return Path(user_input)


def build_context(root: Path) -> AuditContext:
    md_files = list(root.rglob("*.md"))
    stems = {p.stem for p in md_files}
    stem_to_path = {p.stem: p for p in md_files}
    category_files = [p for p in md_files if "/categories/" in p.as_posix()]
    concept_files = [p for p in md_files if "/concepts/" in p.as_posix()]
    faq_files = [p for p in md_files if "/faqs/" in p.as_posix()]
    return AuditContext(
        root=root,
        md_files=md_files,
        stems=stems,
        stem_to_path=stem_to_path,
        index_path=root / "index.md",
        category_files=category_files,
        concept_files=concept_files,
        faq_files=faq_files,
    )


def is_noise_target(stem: str) -> bool:
    return not stem or any(x in stem for x in ("${", '"', "/opt/", "http://", "https://"))


def extract_wikilink_targets(text: str) -> list[str]:
    cleaned = strip_code_blocks(text)
    targets = []
    for link in WIKILINK_RE.findall(cleaned):
        stem = link.strip().removesuffix(".md")
        if stem.startswith(".agent-queue/"):
            continue
        if is_noise_target(stem):
            continue
        targets.append(stem)
    return targets


def extract_md_rel_targets(text: str) -> list[str]:
    cleaned = strip_code_blocks(text)
    targets = []
    for href in MD_REL_LINK_RE.findall(cleaned):
        href = href.strip().split("#")[0].strip()
        if href.startswith("http"):
            continue
        href = href.replace("\\", "/").lstrip("./")
        if not href.endswith(".md"):
            continue
        if not (href.startswith("faqs/") or href.startswith("concepts/") or href.startswith("categories/")):
            continue
        stem = href.split("/")[-1].removesuffix(".md")
        if stem and not is_noise_target(stem):
            targets.append(stem)
    return targets


def check_index_consistency(ctx: AuditContext) -> dict:
    if not ctx.index_path.is_file():
        return {
            "ok": False,
            "reason": "index.md 不存在",
            "missing_on_disk": [],
            "missing_in_index_categories": [],
            "missing_in_index_concepts": [],
        }

    index_text = ctx.index_path.read_text(encoding="utf-8", errors="ignore")
    index_targets = set(extract_wikilink_targets(index_text))

    missing_on_disk = sorted([stem for stem in index_targets if stem not in ctx.stems])[:200]
    category_stems = {p.stem for p in ctx.category_files}
    concept_stems = {p.stem for p in ctx.concept_files}
    missing_in_index_categories = sorted(category_stems - index_targets)
    missing_in_index_concepts = sorted(concept_stems - index_targets)

    ok = not missing_on_disk and not missing_in_index_categories and not missing_in_index_concepts
    return {
        "ok": ok,
        "missing_on_disk": missing_on_disk[:50],
        "missing_in_index_categories": missing_in_index_categories[:50],
        "missing_in_index_concepts": missing_in_index_concepts[:50],
        "stats": {
            "index_targets": len(index_targets),
            "categories_total": len(category_stems),
            "concepts_total": len(concept_stems),
        },
    }


def check_broken_wikilinks(ctx: AuditContext) -> dict:
    broken: list[tuple[str, str]] = []
    for path in ctx.md_files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for stem in extract_wikilink_targets(text):
            if stem not in ctx.stems:
                broken.append((path.as_posix(), stem))
    return {
        "ok": len(broken) == 0,
        "count": len(broken),
        "sample": broken[:80],
    }


def check_orphans(ctx: AuditContext) -> dict:
    incoming = {p.stem: 0 for p in ctx.md_files}
    for path in ctx.md_files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for stem in extract_wikilink_targets(text):
            if stem in incoming:
                incoming[stem] += 1
        for stem in extract_md_rel_targets(text):
            if stem in incoming:
                incoming[stem] += 1

    orphans: list[str] = []
    for path in ctx.md_files:
        ps = path.as_posix()
        if not any(x in ps for x in ("/faqs/", "/concepts/", "/categories/")):
            continue
        if path.name in {"index.md", "overview.md", "log.md"}:
            continue
        if incoming.get(path.stem, 0) == 0:
            orphans.append(ps)
    return {
        "ok": len(orphans) == 0,
        "count": len(orphans),
        "sample": orphans[:80],
    }


def check_category_summary_heading(ctx: AuditContext) -> dict:
    missing = []
    for path in ctx.category_files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if HEADING_CATEGORY_SUMMARY not in text:
            missing.append(path.as_posix())
    return {"ok": len(missing) == 0, "count": len(missing), "sample": missing[:80]}


def normalize_faq_key(stem: str) -> str:
    key = stem.strip().removesuffix(".md")
    key = re.sub(r"-[0-9a-f]{6,8}$", "", key)
    key = key.replace("问答-", "")
    key = re.sub(r"\s+", "", key)
    key = re.sub(r"[？?！!。．,，、；;：:\"'“”【】\[\]()（）\-_/]+", "", key)
    return key.lower()


def check_faq_similarity_merge(ctx: AuditContext, threshold: float) -> dict:
    groups: dict[str, list[str]] = defaultdict(list)
    for path in ctx.faq_files:
        groups[normalize_faq_key(path.stem)].append(path.stem)

    duplicates = [(k, v) for k, v in groups.items() if k and len(v) > 1]
    duplicates.sort(key=lambda x: len(x[1]), reverse=True)

    # 简化近似：同 key 组即可视为“疑似未合并”
    candidates = [{"normalized_key": k, "faq_stems": sorted(v)[:20], "count": len(v)} for k, v in duplicates]
    return {
        "ok": len(candidates) == 0,
        "threshold": threshold,
        "suspected_groups": len(candidates),
        "sample": candidates[:50],
    }


def check_concept_backlinks_and_sessions(ctx: AuditContext) -> dict:
    missing_backlinks = []
    for path in ctx.concept_files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        has_faq_backlink = False
        for stem in extract_wikilink_targets(text):
            target_path = ctx.stem_to_path.get(stem)
            if target_path and "/faqs/" in target_path.as_posix():
                has_faq_backlink = True
                break
        if not has_faq_backlink:
            for stem in extract_md_rel_targets(text):
                target_path = ctx.stem_to_path.get(stem)
                if target_path and "/faqs/" in target_path.as_posix():
                    has_faq_backlink = True
                    break
        if not has_faq_backlink:
            missing_backlinks.append(path.as_posix())

    sessions_dir = ctx.root / "sessions"
    has_sessions_dir = sessions_dir.exists()
    return {
        "ok": (len(missing_backlinks) == 0) and (not has_sessions_dir),
        "missing_backlink_count": len(missing_backlinks),
        "missing_backlink_sample": missing_backlinks[:80],
        "has_sessions_dir": has_sessions_dir,
    }


def normalize_link_key(stem: str) -> str:
    key = stem.strip().removesuffix(".md")
    key = re.sub(r"-[0-9a-f]{6,8}$", "", key)
    key = re.sub(r"\s+", "", key)
    key = re.sub(r"[？?！!。．,，、；;：:\"'“”【】\[\]()（）\-_/]+", "", key)
    return key.lower()


def check_taxonomy_link_similarity_dedup(ctx: AuditContext, threshold: float) -> dict:
    """检查分类/概念页内高相似链接（疑似应合并或去重）。"""
    suspicious_exact: list[dict] = []
    suspicious_near: list[dict] = []
    files = sorted(ctx.category_files + ctx.concept_files, key=lambda p: p.as_posix())

    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        targets = extract_wikilink_targets(text) + extract_md_rel_targets(text)
        if not targets:
            continue

        grouped: dict[str, list[str]] = defaultdict(list)
        for stem in targets:
            grouped[normalize_link_key(stem)].append(stem)

        for key, stems in grouped.items():
            uniq = sorted(set(stems))
            if key and len(uniq) > 1:
                suspicious_exact.append(
                    {
                        "file": path.as_posix(),
                        "normalized_key": key,
                        "link_stems": uniq[:20],
                        "count": len(uniq),
                    }
                )

        uniq_targets = sorted(set(targets))
        for left, right in itertools.combinations(uniq_targets, 2):
            if left == right:
                continue
            left_n = normalize_link_key(left)
            right_n = normalize_link_key(right)
            if not left_n or not right_n:
                continue
            if left_n == right_n:
                continue
            score = SequenceMatcher(None, left_n, right_n).ratio()
            if score >= threshold:
                suspicious_near.append(
                    {
                        "file": path.as_posix(),
                        "left": left,
                        "right": right,
                        "score": round(score, 4),
                    }
                )

    # 避免输出过大
    suspicious_exact = sorted(suspicious_exact, key=lambda x: x["count"], reverse=True)
    suspicious_near = sorted(suspicious_near, key=lambda x: x["score"], reverse=True)
    ok = len(suspicious_exact) == 0 and len(suspicious_near) == 0
    return {
        "ok": ok,
        "threshold": threshold,
        "suspected_same_key_groups": len(suspicious_exact),
        "suspected_high_similarity_pairs": len(suspicious_near),
        "sample_same_key_groups": suspicious_exact[:50],
        "sample_high_similarity_pairs": suspicious_near[:80],
    }


def run_all_checks(ctx: AuditContext, threshold: float, link_threshold: float) -> dict:
    check_1 = check_index_consistency(ctx)
    check_2 = check_orphans(ctx)
    check_3 = check_broken_wikilinks(ctx)
    check_4 = check_category_summary_heading(ctx)
    check_5 = check_faq_similarity_merge(ctx, threshold=threshold)
    check_6 = check_concept_backlinks_and_sessions(ctx)
    check_7 = check_taxonomy_link_similarity_dedup(ctx, threshold=link_threshold)

    all_ok = all(x.get("ok", False) for x in (check_1, check_2, check_3, check_4, check_5, check_6, check_7))
    return {
        "all_ok": all_ok,
        "root": ctx.root.as_posix(),
        "stats": {
            "total_md": len(ctx.md_files),
            "faqs": len(ctx.faq_files),
            "categories": len(ctx.category_files),
            "concepts": len(ctx.concept_files),
        },
        "checks": {
            "1_index_consistency": check_1,
            "2_no_orphans": check_2,
            "3_no_broken_wikilinks": check_3,
            "4_category_has_summary_heading": check_4,
            "5_similar_faq_merged": check_5,
            "6_concept_backlink_and_no_sessions": check_6,
            "7_taxonomy_links_similarity_dedup": check_7,
        },
    }


def main() -> None:
    configure_stdout()
    args = parse_args()

    default_root = Path("e:/py_workspace/langchain-mcp/kefu-know/wiki")
    if args.interactive or args.root is None:
        root = interactive_resolve_root(default_root)
        print("输入 y 执行 7 项检查（其他任意键取消）：")
        confirm = input("> ").strip().lower()
        if confirm != "y":
            print(json.dumps({"cancelled": True}, ensure_ascii=False))
            return
    else:
        root = args.root

    if not root.exists():
        print(json.dumps({"error": f"路径不存在: {root.as_posix()}"}, ensure_ascii=False))
        return

    ctx = build_context(root)
    report = run_all_checks(ctx, threshold=args.threshold, link_threshold=args.link_threshold)

    out = args.output or (root / ".agent-queue" / "kb-audit-lint-report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"all_ok": report["all_ok"], "output": out.as_posix()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
