"""Kefu wiki retrieval aligned with llm-wiki-agent tools/query.py (index + graph + FAQ)."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# llm-wiki-agent query.py caps context pages
MAX_RELEVANT_PAGES = 10
GRAPH_MIN_CONFIDENCE = 0.7

_WIKILINK_RE = re.compile(r"\[\[(分类|问答|概念)-([^\]]+)\]\]")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_SECTION_FAQ_RE = re.compile(
    r"^###\s+\d+\.\s+(.+?)\s*$[\s\S]*?"
    r">\s*详情页：\[\[(问答-[^\]]+)\]\]",
    re.MULTILINE,
)
_STANDARD_ANSWER_RE = re.compile(r"##\s*标准回答\s*\n+([\s\S]*?)(?=\n##\s|\Z)")

@dataclass
class IndexPagePick:
    """LLM 从 index 选中的 wiki 页及已读取正文。"""

    path: Path
    rel_path: str
    content: str


IndexPageSelector = Callable[[str, str, Path], list[IndexPagePick]]


@dataclass
class CategoryEntry:
    title: str
    overview: str
    wikilink: str
    high_freq_questions: list[str] = field(default_factory=list)


@dataclass
class RetrievalHit:
    rel_path: str
    title: str
    excerpt: str
    score: float
    hit_type: str
    source_route: str = ""


@dataclass
class WikiRetrievalResult:
    question: str
    categories: list[RetrievalHit]
    faqs: list[RetrievalHit]
    concepts: list[RetrievalHit]
    keywords: list[str]
    retrieval_notes: list[str] = field(default_factory=list)

    def collect_source_paths(self) -> list[str]:
        seen: set[str] = set()
        paths: list[str] = []
        for hit in self.categories + self.faqs + self.concepts:
            if hit.rel_path and hit.rel_path not in seen:
                seen.add(hit.rel_path)
                paths.append(hit.rel_path)
        return paths

    def to_prompt_text(
        self,
        *,
        max_category_chars: int = 6000,
        max_faq_chars: int = 4000,
        max_concept_chars: int = 2000,
    ) -> str:
        lines = [
            "# 客服知识库检索结果（程序已检索，请据此合成答复）",
            f"用户问题：{self.question}",
            f"检索关键词：{', '.join(self.keywords) if self.keywords else '（无）'}",
        ]
        if self.retrieval_notes:
            lines.append(f"检索说明：{'；'.join(self.retrieval_notes)}")
        lines.extend(
            [
                "",
                "要求：先给结论，再简短步骤；口径以「标准回答」为准；注明来源路径；无依据则说明未收录。",
                "",
            ]
        )
        if self.categories:
            lines.append("## 主题页")
            for hit in self.categories:
                body = hit.excerpt
                if len(body) > max_category_chars:
                    body = body[:max_category_chars] + "\n\n…（主题页已截断）"
                route = f" [{hit.source_route}]" if hit.source_route else ""
                lines.extend([f"### {hit.title}{route}", f"来源：`{hit.rel_path}`", "", body, ""])
        if self.faqs:
            lines.append("## FAQ 标准答")
            for hit in self.faqs:
                body = hit.excerpt
                if len(body) > max_faq_chars:
                    body = body[:max_faq_chars] + "\n\n…（FAQ 已截断）"
                route = f" [{hit.source_route}]" if hit.source_route else ""
                lines.extend([f"### {hit.title}{route}", f"来源：`{hit.rel_path}`", "", body, ""])
        if self.concepts:
            lines.append("## 相关概念")
            for hit in self.concepts:
                body = hit.excerpt
                if len(body) > max_concept_chars:
                    body = body[:max_concept_chars] + "\n\n…（概念页已截断）"
                route = f" [{hit.source_route}]" if hit.source_route else ""
                lines.extend([f"### {hit.title}{route}", f"来源：`{hit.rel_path}`", "", body, ""])
        if self.categories or self.faqs or self.concepts:
            lines.append("## Sources")
            for hit in self.categories + self.faqs + self.concepts:
                lines.append(f"- `{hit.rel_path}`")
        else:
            lines.append("（未命中 wiki 内容，请明确告知用户知识库未收录该问题。）")
        return "\n".join(lines).strip()


def title_matches_question(title: str, question: str) -> bool:
    """Same CJK bigram / Latin word rules as llm-wiki-agent tools/query.py."""
    title_lower = title.lower()
    question_lower = question.lower()
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in title)
    if has_cjk:
        return any(
            title_lower[j : j + 2] in question_lower
            for j in range(len(title_lower) - 1)
            if any("\u4e00" <= c <= "\u9fff" for c in title_lower[j : j + 2])
        )
    return any(word in question_lower for word in title_lower.split() if len(word) > 2)


def wiki_page_id(path: Path, wiki_root: Path) -> str:
    return path.relative_to(wiki_root).as_posix().replace(".md", "")


def find_relevant_pages_from_index(question: str, index_content: str, wiki_root: Path) -> list[Path]:
    """Match index markdown links and ### section titles (llm-wiki query.py)."""
    relevant: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        if path.is_file() and path not in seen:
            seen.add(path)
            relevant.append(path)

    for title, href in _MD_LINK_RE.findall(index_content):
        if title_matches_question(title, question):
            add((wiki_root / href).resolve())

    blocks = re.split(r"\n###\s+", index_content)
    for block in blocks[1:]:
        lines = block.strip().splitlines()
        if not lines:
            continue
        section_title = lines[0].strip()
        block_matched = title_matches_question(section_title, question)
        if not block_matched:
            for line in lines[1:]:
                if line.strip().startswith("- ") and title_matches_question(line.strip()[2:], question):
                    block_matched = True
                    break
        if not block_matched:
            continue
        for line in lines[1:]:
            link_match = _WIKILINK_RE.search(line)
            if link_match and link_match.group(1) == "分类":
                wikilink = f"分类-{link_match.group(2)}"
                cat_path = resolve_wiki_file(wiki_root, "categories", wikilink)
                if cat_path is not None:
                    add(cat_path)

    return relevant


def expand_pages_via_graph(
    pages: list[Path],
    wiki_root: Path,
    repo_root: Path | None = None,
    *,
    min_confidence: float = GRAPH_MIN_CONFIDENCE,
) -> list[Path]:
    """One-hop neighbor expansion from graph/graph.json (llm-wiki query.py)."""
    if not pages:
        return pages
    graph_json = (repo_root or wiki_root.parent) / "graph" / "graph.json"
    if not graph_json.is_file():
        return pages

    try:
        graph_data = json.loads(graph_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return pages

    page_ids = {wiki_page_id(p, wiki_root) for p in pages}
    neighbors: set[str] = set()
    for edge in graph_data.get("edges", []):
        if float(edge.get("confidence", 0)) < min_confidence:
            continue
        src = edge.get("from")
        dst = edge.get("to")
        if src in page_ids:
            neighbors.add(dst)
        elif dst in page_ids:
            neighbors.add(src)

    merged = list(pages)
    seen = set(pages)
    for node_id in neighbors:
        neighbor_path = wiki_root / f"{node_id}.md"
        if neighbor_path.is_file() and neighbor_path not in seen:
            seen.add(neighbor_path)
            merged.append(neighbor_path)
    return merged


def has_category_relation_match(
    question: str,
    keywords: list[str],
    categories_meta: list[CategoryEntry],
    ordered_paths: list[Path],
    path_routes: dict[Path, str],
) -> bool:
    """是否已通过 index/主题分类关键词建立分类关联。"""
    for path in ordered_paths:
        route = path_routes.get(path, "")
        if route == "index_match" and path.name != "overview.md":
            return True

    if not categories_meta:
        return False

    picked = rank_categories(question, keywords, categories_meta, limit=2)
    if not picked:
        return False
    top_score = score_text(
        keywords,
        picked[0].title,
        picked[0].overview,
        picked[0].wikilink,
        " ".join(picked[0].high_freq_questions),
        question,
    )
    return top_score > 0


def should_use_index_llm_select(
    question: str,
    keywords: list[str],
    categories_meta: list[CategoryEntry],
    ordered_paths: list[Path],
    path_routes: dict[Path, str],
) -> bool:
    """index/分类规则未建立有效关联，或 index 仅弱命中时，用 LLM 从 index 选页。"""
    non_overview = [path for path in ordered_paths if path.name != "overview.md"]
    weak_index = not non_overview or len(non_overview) <= 1
    no_category_relation = not has_category_relation_match(
        question,
        keywords,
        categories_meta,
        ordered_paths,
        path_routes,
    )
    return weak_index or no_category_relation


def select_index_pages_via_llm(
    question: str,
    index_content: str,
    wiki_root: Path,
    *,
    invoke_llm: Callable[[str], str],
    max_pages: int = 5,
    max_chars_per_page: int = 12000,
) -> list[IndexPagePick]:
    """index/分类规则未命中时，由大模型从 index.md 选页并读取各页正文后返回。"""
    prompt = (
        "你是超算互联网客服知识库检索助手。请根据 wiki/index.md 为用户问题选择最相关的文档页面。\n\n"
        f"## index.md\n\n{index_content}\n\n"
        f'## 用户问题\n"{question}"\n\n'
        "只返回 JSON 数组，元素为 wiki 根目录下的相对路径（须对应 index 中出现的主题页、FAQ 或 concepts 链接），例如：\n"
        '["categories/分类-账户充值与计费-订单-发票-Token-购买.md", "faqs/问答-xxx.md"]\n'
        f"最多 {max_pages} 条；不要返回 index.md 或 overview.md；不要解释。"
    )
    raw = invoke_llm(prompt).strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        paths = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("wiki index LLM selector returned invalid JSON")
        return []

    resolved: list[IndexPagePick] = []
    if not isinstance(paths, list):
        return resolved
    for item in paths:
        if not isinstance(item, str):
            continue
        rel = item.replace("\\", "/").lstrip("/")
        candidate = wiki_root / rel
        if not candidate.is_file():
            logger.warning("wiki index LLM path not found: %s", rel)
            continue
        content = read_text(candidate, max_chars=max_chars_per_page)
        resolved.append(
            IndexPagePick(
                path=candidate,
                rel_path=str(candidate.relative_to(wiki_root)).replace("\\", "/"),
                content=content,
            )
        )
    return resolved


def extract_keywords(question: str) -> list[str]:
    """Extract Chinese / Latin tokens for matching (no jieba)."""
    text = question.strip()
    if not text:
        return []

    seen: set[str] = set()
    tokens: list[str] = []

    def add(token: str) -> None:
        token = token.strip().lower()
        if len(token) < 2 or token in seen:
            return
        seen.add(token)
        tokens.append(token)

    for match in re.finditer(r"[\u4e00-\u9fff]{2,}", text):
        segment = match.group()
        add(segment)
        if len(segment) >= 3:
            for size in (2, 3, 4):
                if size > len(segment):
                    continue
                for i in range(len(segment) - size + 1):
                    add(segment[i : i + size])

    for match in re.finditer(r"[A-Za-z][A-Za-z0-9_.-]{1,}", text):
        add(match.group())

    return tokens[:40]


def parse_index_categories(index_text: str) -> list[CategoryEntry]:
    """Parse `wiki/index.md` theme sections."""
    categories: list[CategoryEntry] = []
    blocks = re.split(r"\n###\s+", index_text)
    for block in blocks[1:]:
        lines = block.strip().splitlines()
        if not lines:
            continue
        title = lines[0].strip()
        wikilink = ""
        overview_lines: list[str] = []
        high_freq: list[str] = []
        in_overview = False
        in_high_freq = False

        for line in lines[1:]:
            link_match = _WIKILINK_RE.search(line)
            if link_match and link_match.group(1) == "分类" and not wikilink:
                wikilink = f"分类-{link_match.group(2)}"
            if line.strip().startswith("涵盖") or (not wikilink and line.strip() and not line.startswith("-")):
                in_overview = True
                overview_lines.append(line.strip())
                continue
            if "高频问题" in line:
                in_high_freq = True
                in_overview = False
                continue
            if in_high_freq and line.strip().startswith("- "):
                q = line.strip()[2:].strip()
                if q and not q.startswith("主题页"):
                    high_freq.append(q)
            elif in_overview and line.strip() and not line.startswith("- "):
                overview_lines.append(line.strip())

        overview = " ".join(overview_lines).strip()
        if wikilink:
            categories.append(
                CategoryEntry(
                    title=title,
                    overview=overview,
                    wikilink=wikilink,
                    high_freq_questions=high_freq,
                )
            )
    return categories


def score_text(keywords: list[str], *texts: str) -> float:
    if not keywords:
        return 0.0
    blob = " ".join(texts).lower()
    return float(sum(1 for kw in keywords if kw in blob))


def rank_categories(question: str, keywords: list[str], categories: list[CategoryEntry], limit: int = 2) -> list[CategoryEntry]:
    scored: list[tuple[float, CategoryEntry]] = []
    for entry in categories:
        score = score_text(
            keywords,
            entry.title,
            entry.overview,
            entry.wikilink,
            " ".join(entry.high_freq_questions),
            question,
        )
        scored.append((score, entry))
    scored.sort(key=lambda item: item[0], reverse=True)
    if scored and scored[0][0] > 0:
        return [entry for _, entry in scored[:limit]]
    return categories[:limit]


def resolve_wiki_file(wiki_root: Path, folder: str, link_name: str) -> Path | None:
    """Resolve `问答-xxx` / `分类-xxx` to an on-disk markdown file."""
    target_dir = wiki_root / folder
    if not target_dir.is_dir():
        return None
    exact = target_dir / f"{link_name}.md"
    if exact.is_file():
        return exact
    prefix = link_name.split("（")[0].strip()
    candidates = sorted(target_dir.glob(f"{prefix}*.md"))
    if candidates:
        return candidates[0]
    return None


def read_text(path: Path, max_chars: int | None = None) -> str:
    text = path.read_text(encoding="utf-8")
    if max_chars is not None and len(text) > max_chars:
        return text[:max_chars] + "\n\n…（已截断）"
    return text


def extract_standard_answer(markdown: str) -> str:
    match = _STANDARD_ANSWER_RE.search(markdown)
    if match:
        return match.group(1).strip()
    return ""


def extract_faq_links_from_category(category_text: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for match in _SECTION_FAQ_RE.finditer(category_text):
        raw = match.group(2).strip()
        name = raw.split("（")[0].strip()
        if name.startswith("问答-") and name not in seen:
            seen.add(name)
            names.append(name)
    for match in _WIKILINK_RE.finditer(category_text):
        if match.group(1) == "问答":
            name = f"问答-{match.group(2)}".split("（")[0].strip()
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def rank_faq_files(
    wiki_root: Path,
    keywords: list[str],
    question: str,
    preferred_names: list[str],
    limit: int = 5,
) -> list[Path]:
    faqs_dir = wiki_root / "faqs"
    if not faqs_dir.is_dir():
        return []

    scored: list[tuple[float, Path]] = []
    seen_paths: set[Path] = set()

    for name in preferred_names:
        path = resolve_wiki_file(wiki_root, "faqs", name)
        if path is None or path in seen_paths:
            continue
        seen_paths.add(path)
        text = read_text(path, max_chars=8000)
        title = path.stem.replace("问答-", "", 1)
        score = score_text(keywords, title, text, question) + 2.0
        scored.append((score, path))

    for path in faqs_dir.glob("*.md"):
        if path in seen_paths:
            continue
        title = path.stem
        filename_score = score_text(keywords, title)
        if filename_score <= 0:
            continue
        seen_paths.add(path)
        scored.append((filename_score, path))

    if len(scored) < limit:
        for path in faqs_dir.glob("*.md"):
            if path in seen_paths:
                continue
            text = read_text(path, max_chars=12000)
            content_score = score_text(keywords, text, question)
            if content_score <= 0:
                continue
            seen_paths.add(path)
            scored.append((content_score * 0.8, path))
            if len(scored) >= limit * 4:
                break

    scored.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in scored[:limit]]


def rank_concept_files(wiki_root: Path, keywords: list[str], limit: int = 2) -> list[Path]:
    concepts_dir = wiki_root / "concepts"
    if not concepts_dir.is_dir():
        return []
    scored: list[tuple[float, Path]] = []
    for path in concepts_dir.glob("*.md"):
        score = score_text(keywords, path.stem, read_text(path, max_chars=4000))
        if score > 0:
            scored.append((score, path))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in scored[:limit]]


def _hit_type_for_path(path: Path, wiki_root: Path) -> str:
    try:
        rel = path.relative_to(wiki_root).parts
    except ValueError:
        return "page"
    if rel and rel[0] == "categories":
        return "category"
    if rel and rel[0] == "faqs":
        return "faq"
    if rel and rel[0] == "concepts":
        return "concept"
    if path.name == "overview.md":
        return "overview"
    return "page"


def path_to_hit(
    path: Path,
    wiki_root: Path,
    *,
    keywords: list[str],
    question: str,
    source_route: str,
    preloaded_text: str | None = None,
) -> RetrievalHit | None:
    if not path.is_file() and preloaded_text is None:
        return None
    rel_path = str(path.relative_to(wiki_root)).replace("\\", "/")
    hit_type = _hit_type_for_path(path, wiki_root)
    full_text = preloaded_text if preloaded_text is not None else read_text(path)

    if hit_type == "faq":
        answer = extract_standard_answer(full_text)
        title = path.stem.replace("问答-", "", 1)
        excerpt = f"## 客户问法\n（见原文）\n\n## 标准回答\n\n{answer}" if answer else full_text[:4000]
    elif hit_type == "concept":
        title = path.stem.replace("概念-", "", 1)
        excerpt = read_text(path, max_chars=2500)
    elif hit_type == "category":
        title = path.stem.replace("分类-", "", 1)
        excerpt = full_text
    elif hit_type == "overview":
        title = "知识库总览"
        excerpt = read_text(path, max_chars=4000)
    else:
        title = path.stem
        excerpt = read_text(path, max_chars=4000)

    return RetrievalHit(
        rel_path=rel_path,
        title=title,
        excerpt=excerpt,
        score=score_text(keywords, title, full_text, question),
        hit_type=hit_type,
        source_route=source_route,
    )


def _merge_hits(
    categories: list[RetrievalHit],
    faqs: list[RetrievalHit],
    concepts: list[RetrievalHit],
    new_hit: RetrievalHit,
    *,
    max_categories: int,
    max_faqs: int,
    max_concepts: int,
) -> None:
    bucket_map = {
        "category": (categories, max_categories),
        "faq": (faqs, max_faqs),
        "concept": (concepts, max_concepts),
        "overview": (categories, 1),
        "page": (categories, max_categories),
    }
    bucket, limit = bucket_map.get(new_hit.hit_type, (categories, max_categories))
    if any(item.rel_path == new_hit.rel_path for item in bucket):
        return
    if len(bucket) >= limit:
        return
    bucket.append(new_hit)


def retrieve_kefu_wiki(
    question: str,
    wiki_root: Path | str,
    *,
    repo_root: Path | str | None = None,
    max_categories: int = 2,
    max_faqs: int = 5,
    max_concepts: int = 2,
    max_pages: int = MAX_RELEVANT_PAGES,
    index_page_selector: IndexPageSelector | None = None,
) -> WikiRetrievalResult:
    """客服 wiki 混合检索主入口。

    流程概要：
    1. 读 index.md，extract_keywords 抽检索词；
    2. 阶段 A：overview → index 标题/高频/链接匹配 → graph 邻居 → 可选 LLM 选页；
    3. 阶段 B：关键词 rank 主题 → 合并路径为 RetrievalHit → FAQ/概念补召回；
    4. 返回 WikiRetrievalResult，由 query_kefu_wiki 工具格式化为 prompt 证据包。

    检索阶段默认 0 次 LLM；index/分类未建立关联或最终无命中时，由 index_page_selector 读 index.md 选页（需开启配置）。
    """
    root = Path(wiki_root)
    repo = Path(repo_root) if repo_root is not None else root.parent
    index_path = root / "index.md"
    notes: list[str] = []

    if not index_path.is_file():
        return WikiRetrievalResult(question=question, categories=[], faqs=[], concepts=[], keywords=[], retrieval_notes=[])

    index_content = read_text(index_path)
    keywords = extract_keywords(question)
    categories_meta = parse_index_categories(index_content)
    index_llm_used = False
    llm_page_contents: dict[Path, str] = {}

    # 阶段 A 的候选路径（保持 enqueue 顺序 = 优先级），path_routes 记录命中原因供调试
    ordered_paths: list[Path] = []
    path_routes: dict[Path, str] = {}

    def enqueue(path: Path, route: str) -> None:
        if path not in path_routes:
            ordered_paths.append(path)
            path_routes[path] = route

    # 与 llm-wiki query.py 相同：总览页始终加入，提供跨主题背景
    overview = root / "overview.md"
    if overview.is_file():
        enqueue(overview, "overview")

    # --- 阶段 A：index / graph 路由（对齐 llm-wiki query.py，无 LLM）---
    # 扫描 index：### 主题标题、高频问题、[title](href)、[[分类-...]] → categories/*.md
    for path in find_relevant_pages_from_index(question, index_content, root):
        enqueue(path, "index_match")

    # 若存在 kefu-know/graph/graph.json，对当前候选做 1 跳邻居扩展（边 confidence≥0.7）
    expanded = expand_pages_via_graph(ordered_paths, root, repo)
    if len(expanded) > len(ordered_paths):
        notes.append("graph_neighbor")
        for path in expanded:
            if path not in path_routes:
                enqueue(path, "graph_neighbor")

    # index/分类规则未建立关联，或 index 命中过弱：Ark 读 index.md 选最相关 n 页，再载入正文（query_kefu_wiki 证据包）
    if index_page_selector is not None and should_use_index_llm_select(
        question,
        keywords,
        categories_meta,
        ordered_paths,
        path_routes,
    ):
        notes.append("index_llm_select")
        index_llm_used = True
        for pick in index_page_selector(question, index_content, root):
            enqueue(pick.path, "index_llm_select")
            llm_page_contents[pick.path] = pick.content

    # 控制送入证据包的页面总量，避免超过模型上下文（默认 15，同 query.py）
    ordered_paths = ordered_paths[:max_pages]

    # --- 阶段 B：关键词补召回 + 与 index 路由结果合并 ---
    picked_categories = rank_categories(question, keywords, categories_meta, limit=max_categories)
    # 主题页内 [[问答-...]] 链接，供 FAQ 检索优先读取（高分 + 减少全库扫描）
    preferred_faq_names: list[str] = []

    category_hits: list[RetrievalHit] = []
    faq_hits: list[RetrievalHit] = []
    concept_hits: list[RetrievalHit] = []

    # B1. 将阶段 A 的页面转为 RetrievalHit（按路径所在目录归入 category/faq/concept/overview）
    for path in ordered_paths:
        hit = path_to_hit(
            path,
            root,
            keywords=keywords,
            question=question,
            source_route=path_routes.get(path, ""),
            preloaded_text=llm_page_contents.get(path),
        )
        if hit is None:
            continue
        _merge_hits(category_hits, faq_hits, concept_hits, hit, max_categories=max_categories, max_faqs=max_faqs, max_concepts=max_concepts)
        if hit.hit_type == "category":
            preferred_faq_names.extend(extract_faq_links_from_category(hit.excerpt))

    # B2. 关键词排名的主题页（最多 max_categories），与 index_match 去重合并
    for entry in picked_categories:
        cat_path = resolve_wiki_file(root, "categories", entry.wikilink)
        if cat_path is None:
            continue
        preferred_faq_names.extend(extract_faq_links_from_category(read_text(cat_path)))
        hit = path_to_hit(
            cat_path,
            root,
            keywords=keywords,
            question=question,
            source_route="category_rank",
        )
        if hit is not None:
            _merge_hits(category_hits, faq_hits, concept_hits, hit, max_categories=max_categories, max_faqs=max_faqs, max_concepts=max_concepts)

    # B3. FAQ：优先 preferred 链接，再按文件名/正文关键词打分，FAQ 摘录「标准回答」字段
    for path in rank_faq_files(root, keywords, question, preferred_faq_names, limit=max_faqs):
        hit = path_to_hit(path, root, keywords=keywords, question=question, source_route="keyword_faq")
        if hit is not None:
            _merge_hits(category_hits, faq_hits, concept_hits, hit, max_categories=max_categories, max_faqs=max_faqs, max_concepts=max_concepts)

    # B4. 概念页：按关键词匹配 concepts/*.md（数量少，全目录扫描可接受）
    for path in rank_concept_files(root, keywords, limit=max_concepts):
        hit = path_to_hit(path, root, keywords=keywords, question=question, source_route="keyword_concept")
        if hit is not None:
            _merge_hits(category_hits, faq_hits, concept_hits, hit, max_categories=max_categories, max_faqs=max_faqs, max_concepts=max_concepts)

    # 规则检索仍无实质内容时，最后再走一次 index LLM 选页并加载文档
    if (
        not category_hits
        and not faq_hits
        and not concept_hits
        and index_page_selector is not None
        and not index_llm_used
    ):
        notes.append("index_llm_select")
        for pick in index_page_selector(question, index_content, root):
            hit = path_to_hit(
                pick.path,
                root,
                keywords=keywords,
                question=question,
                source_route="index_llm_select",
                preloaded_text=pick.content,
            )
            if hit is None:
                continue
            _merge_hits(
                category_hits,
                faq_hits,
                concept_hits,
                hit,
                max_categories=max_categories,
                max_faqs=max_faqs,
                max_concepts=max_concepts,
            )
            if hit.hit_type == "category":
                preferred_faq_names.extend(extract_faq_links_from_category(hit.excerpt))

    # 检索说明写入结果，便于调试与 prompt 内展示（使用了哪些通道）
    if "index_match" in path_routes.values() or any(h.source_route == "index_match" for h in category_hits + faq_hits):
        notes.append("index_match")
    if any(h.source_route == "keyword_faq" for h in faq_hits):
        notes.append("keyword_faq")

    return WikiRetrievalResult(
        question=question,
        categories=category_hits,
        faqs=faq_hits,
        concepts=concept_hits,
        keywords=keywords,
        retrieval_notes=notes,
    )
