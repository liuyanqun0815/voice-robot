#!/usr/bin/env python3
"""生成 FAQ 草稿待审核清单（按分类分组）。"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path


def find_project_root(script_dir: Path) -> Path:
    cur = script_dir
    for _ in range(10):
        if (cur / "kefu-know").is_dir():
            return cur
        cur = cur.parent
    raise SystemExit("未找到 kefu-know 目录")


def parse_draft(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m_cat = re.search(r'^category:\s*"\[\[(.+?)\]\]"', text, re.M)
    m_q = re.search(r"^# (.+)$", text, re.M)
    m_sum = re.search(r"## 会话摘要\s*\n+(.+)", text)
    return {
        "file": path.name,
        "stem": path.stem,
        "category": m_cat.group(1).strip() if m_cat else "未分类",
        "question": m_q.group(1).strip() if m_q else path.stem,
        "summary": (m_sum.group(1).strip() if m_sum else "")[:80],
    }


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    project_root = find_project_root(script_dir)
    wiki_dir = project_root / "kefu-know" / "wiki"
    drafts_dir = wiki_dir / ".agent-queue" / "faq-drafts"
    out_path = wiki_dir / ".agent-queue" / "draft-review-index.md"

    items = [parse_draft(p) for p in sorted(drafts_dir.glob("*.md"))]
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        by_cat[item["category"]].append(item)

    lines: list[str] = []
    lines.append("# FAQ 草稿待审核清单")
    lines.append("")
    lines.append(f"- 草稿总数：**{len(items)}**")
    lines.append(f"- 分类数：**{len(by_cat)}**")
    lines.append("")
    lines.append("## 按分类分组")
    lines.append("")

    for cat in sorted(by_cat):
        group = by_cat[cat]
        lines.append(f"### {cat}（{len(group)}）")
        for item in group[:200]:
            lines.append(f"- [[.agent-queue/faq-drafts/{item['stem']}|{item['question']}]]")
        if len(group) > 200:
            lines.append(f"- ... 其余 {len(group) - 200} 条")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(out_path.as_posix())


if __name__ == "__main__":
    main()
