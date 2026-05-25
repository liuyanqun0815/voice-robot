#!/usr/bin/env python3
"""全量会话 Agent 萃取：通读 raw → JSON + FAQ 草稿 markdown（含 wikilink）。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from agent_ingest import render_extraction_markdown, session_id_from_path  # noqa: E402
from ingest_wiki import (  # noqa: E402
    CATEGORIES,
    CONCEPT_DEFS,
    extract_heuristic,
    infer_category_id,
    load_existing_category_names,
    parse_session_file,
)


def find_project_root() -> Path:
    candidate = SCRIPT_DIR
    for _ in range(10):
        if (candidate / "kefu-know").is_dir():
            return candidate
        candidate = candidate.parent
    raise SystemExit("未找到 kefu-know 目录")


def load_concept_wikilinks(wiki_dir: Path) -> list[tuple[str, list[str]]]:
    """(wikilink_stem, match_patterns) 来自 wiki/concepts 与 CONCEPT_DEFS。"""
    items: list[tuple[str, list[str]]] = []
    concepts_dir = wiki_dir / "concepts"
    if concepts_dir.is_dir():
        for path in concepts_dir.glob("*.md"):
            stem = path.stem
            text = path.read_text(encoding="utf-8")[:800]
            m_title = re.search(r"^# (.+)$", text, re.M)
            title = m_title.group(1) if m_title else stem
            patterns = [re.escape(title[:20]), stem.replace("概念-", "")]
            items.append((stem, patterns))
    for _cid, (title, _defn, patterns) in CONCEPT_DEFS.items():
        safe = re.sub(r"[^\w\u4e00-\u9fff（）()\- ]", "", title)
        stem_guess = f"概念-{safe}"
        items.append((stem_guess, patterns))
    return items


def infer_related_wikilinks(
    blob: str,
    cat_id: str,
    category_map: dict[str, str],
    concept_items: list[tuple[str, list[str]]],
) -> list[str]:
    links: list[str] = []
    if cat_id in category_map:
        links.append(category_map[cat_id])
    lower = blob.lower()
    for stem, patterns in concept_items:
        if any(re.search(p, blob, re.I) or p.lower() in lower for p in patterns if p):
            if stem not in links:
                links.append(stem)
    return links[:12]


def _link_block_field(block, name: str, default: str = "") -> str:
    if hasattr(block, name):
        return getattr(block, name) or default
    if isinstance(block, dict):
        return block.get(name, default) or default
    return default


def synthesize_answer(ext: dict) -> str:
    """整合客服口径与链接附件要点，避免 answer 仅一条 URL。"""
    answer = (ext.get("answer") or "").strip()
    link_blocks = ext.get("link_blocks") or []
    urls_in_answer = set(re.findall(r"https?://\S+", answer))
    if len(answer) < 30 and urls_in_answer and link_blocks:
        tips: list[str] = []
        for block in link_blocks[:2]:
            body = _link_block_field(block, "body")
            for line in body.split("\n"):
                line = line.strip()
                if 20 <= len(line) <= 200 and not line.startswith("#"):
                    tips.append(line[:180])
                    break
        if tips:
            answer = "；".join(tips) + "\n\n参考：" + "\n".join(sorted(urls_in_answer)[:3])
    if link_blocks and len(answer) < 80:
        extra = []
        for block in link_blocks[:1]:
            body = _link_block_field(block, "body")
            for line in body.split("\n"):
                s = line.strip()
                if 25 <= len(s) <= 160:
                    extra.append(s)
                if len(extra) >= 2:
                    break
        if extra:
            answer = (answer + "\n\n" + "\n".join(extra)).strip()
    return answer


def make_summary(question: str, answer: str, session_id: str) -> str:
    q = question.rstrip("？?")[:50]
    a_line = re.sub(r"https?://\S+", "", answer).split("\n")[0].strip()[:100]
    return f"会话 {session_id[:8]}…：访客问「{q}」；{a_line or '客服已给出指引。'}"


def normalize_steps(steps: list[str]) -> list[str]:
    out: list[str] = []
    for s in steps:
        s = re.sub(r"\s+", " ", s.strip())
        if len(s) < 6:
            continue
        if len(s) > 160:
            s = s[:157] + "…"
        if s not in out:
            out.append(s)
    return out[:10]


def should_skip(data: dict) -> bool:
    blob = " ".join(f"{r}: {c}" for r, c in data.get("transcript", []))
    if len(blob.strip()) < 12:
        return True
    agent = [c for r, c in data["transcript"] if r == "客服"]
    visitor = [c for r, c in data["transcript"] if r == "访客"]
    if not agent or not visitor:
        return True
    if re.search(r"风玫瑰图|漯河市", blob):
        return True
    return False


def extract_session(
    data: dict,
    session_id: str,
    category_map: dict[str, str],
    concept_items: list[tuple[str, list[str]]],
) -> dict | None:
    if should_skip(data):
        return None
    ext = extract_heuristic(data)
    if not ext:
        return None
    ext["answer"] = synthesize_answer(ext)
    if len(ext["answer"]) < 8 and not ext.get("link_blocks"):
        return None
    cat_id = ext.get("category_id") or infer_category_id(ext["question"] + ext["answer"])
    ext["category_id"] = cat_id
    ext["category_title"] = CATEGORIES.get(cat_id, CATEGORIES["general"])[0]
    ext["question"] = ext["question"].rstrip("？?") + "？"
    ext["steps"] = normalize_steps(ext.get("steps") or [])
    ext["summary"] = make_summary(ext["question"], ext["answer"], session_id)
    blob = ext["question"] + ext["answer"] + data.get("link_section", "")
    ext["related_wikilinks"] = infer_related_wikilinks(blob, cat_id, category_map, concept_items)
    ext["tags"] = list(dict.fromkeys(ext.get("tags") or []))[:8]
    ext["source_session_id"] = session_id
    return ext


def main() -> None:
    parser = argparse.ArgumentParser(description="全量 Agent 会话萃取")
    parser.add_argument("--chats-dir", type=Path, default=None)
    parser.add_argument("--wiki-dir", type=Path, default=None)
    parser.add_argument("--force", action="store_true", help="覆盖已有 extraction")
    parser.add_argument("--write-drafts", action="store_true", help="写入 FAQ 草稿 markdown")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    project_root = find_project_root()
    root = project_root / "kefu-know"
    chats_dir = args.chats_dir or root / "raw" / "chats"
    wiki_dir = args.wiki_dir or root / "wiki"
    ext_dir = wiki_dir / ".agent-queue" / "extractions"
    draft_dir = wiki_dir / ".agent-queue" / "faq-drafts"
    ext_dir.mkdir(parents=True, exist_ok=True)
    if args.write_drafts:
        draft_dir.mkdir(parents=True, exist_ok=True)

    category_map = load_existing_category_names(wiki_dir)
    concept_items = load_concept_wikilinks(wiki_dir)

    files = sorted(chats_dir.glob("session_*.md"))
    if args.limit > 0:
        files = files[: args.limit]

    written = skipped = errors = 0
    for path in files:
        sid = session_id_from_path(path)
        json_path = ext_dir / f"{sid}.json"
        if json_path.exists() and not args.force:
            continue
        try:
            data = parse_session_file(path)
            ext = extract_session(data, sid, category_map, concept_items)
            if not ext:
                json_path.write_text(json.dumps({"skip": True}, ensure_ascii=False), encoding="utf-8")
                skipped += 1
                continue
            link_blocks = ext.pop("link_blocks", None)
            links = ext.pop("links", None)
            json_path.write_text(json.dumps(ext, ensure_ascii=False, indent=2), encoding="utf-8")
            if args.write_drafts:
                cat_link = category_map.get(ext["category_id"], ext.get("category_title", ""))
                draft_path = draft_dir / f"{sid}.md"
                draft_path.write_text(
                    render_extraction_markdown(ext, cat_link, link_blocks=link_blocks),
                    encoding="utf-8",
                )
            written += 1
        except Exception as exc:
            errors += 1
            print(f"Error {path.name}: {exc}")

    stats = {
        "written": written,
        "skipped": skipped,
        "errors": errors,
        "total_files": len(files),
        "extractions_dir": str(ext_dir.as_posix()),
    }
    (wiki_dir / ".agent-queue" / "extract-stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
