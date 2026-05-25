#!/usr/bin/env python3
"""分类/概念数据结构解析；taxonomy 由上层 Agent 生成，本模块不调用 LLM API。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CategoryDef:
    id: str
    title: str
    overview: str


@dataclass
class ConceptDef:
    id: str
    title: str
    definition: str
    related_doc_indices: list[int] = field(default_factory=list)
    points: list[str] = field(default_factory=list)
    category_ids: list[str] = field(default_factory=list)


def parse_categories(data: dict) -> dict[str, CategoryDef]:
    cats: dict[str, CategoryDef] = {}
    for item in data.get("categories", []):
        cid = re.sub(r"[^\w\-]", "-", str(item.get("id", "")).strip().lower()).strip("-")
        if not cid:
            continue
        title = str(item.get("title", cid)).strip()
        overview = str(item.get("overview", "")).strip()
        cats[cid] = CategoryDef(id=cid, title=title, overview=overview or title)
    if not cats:
        cats["general"] = CategoryDef(id="general", title="通用文档", overview="未单独归类的文档。")
    return cats


def parse_concepts(data: dict) -> list[ConceptDef]:
    concepts: list[ConceptDef] = []
    for item in data.get("concepts", []):
        cid = re.sub(r"[^\w\-]", "-", str(item.get("id", "")).strip().lower()).strip("-")
        if not cid:
            continue
        indices = [int(x) for x in item.get("related_doc_indices", []) if isinstance(x, (int, float))]
        concepts.append(
            ConceptDef(
                id=cid,
                title=str(item.get("title", cid)).strip(),
                definition=str(item.get("definition", "")).strip(),
                related_doc_indices=indices,
            )
        )
    return concepts


def enrich_concept_points(
    concepts: list[ConceptDef],
    docs: list[Any],
    categories: dict[str, CategoryDef],
) -> None:
    for c in concepts:
        points: list[str] = []
        cat_ids: set[str] = set()
        for idx in c.related_doc_indices:
            if idx < 0 or idx >= len(docs):
                continue
            doc = docs[idx]
            cat_ids.add(getattr(doc, "category_id", "") or "")
            for line in (getattr(doc, "content", "") or "").split("\n")[:15]:
                line = line.strip()
                if 25 <= len(line) <= 140 and not line.startswith("#"):
                    points.append(line[:120])
        c.points = list(dict.fromkeys(points))[:8]
        c.category_ids = sorted(x for x in cat_ids if x)


def save_taxonomy_json(
    path: Any,
    categories: dict[str, CategoryDef],
    concepts: list[ConceptDef],
    source: str = "cursor-agent",
) -> None:
    from datetime import datetime, timezone

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "categories": [{"id": c.id, "title": c.title, "overview": c.overview} for c in categories.values()],
        "concepts": [
            {
                "id": c.id,
                "title": c.title,
                "definition": c.definition,
                "related_doc_indices": c.related_doc_indices,
            }
            for c in concepts
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def heuristic_taxonomy(docs: list[Any]) -> tuple[dict[str, CategoryDef], list[ConceptDef], dict[int, str]]:
    """无 taxonomy 文件时按来源站点粗分。"""
    by_site: dict[str, list[Any]] = {}
    for d in docs:
        site = getattr(d, "source_site", "unknown") or "unknown"
        by_site.setdefault(site, []).append(d)

    categories: dict[str, CategoryDef] = {}
    assignments: dict[int, str] = {}
    for site, site_docs in by_site.items():
        cid = re.sub(r"[^\w\-]", "-", site.lower())
        categories[cid] = CategoryDef(
            id=cid,
            title=f"{site} 文档集",
            overview=f"来自 {site} 的抓取文档（规则回退）。",
        )
    if not categories:
        categories["general"] = CategoryDef(id="general", title="通用文档", overview="全部文档。")

    for i, doc in enumerate(docs):
        site = getattr(doc, "source_site", "general") or "general"
        cid = re.sub(r"[^\w\-]", "-", site.lower())
        doc.category_id = cid if cid in categories else next(iter(categories))
        assignments[i] = doc.category_id

    return categories, [], assignments
