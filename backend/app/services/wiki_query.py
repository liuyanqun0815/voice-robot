"""Kefu wiki retrieval aligned with llm-wiki-agent tools/query.py (index + graph + FAQ)."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

try:
    import jieba  # pyright: ignore[reportMissingImports]
except ImportError:  # pragma: no cover
    jieba = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# llm-wiki-agent query.py caps context pages
MAX_RELEVANT_PAGES = 10
GRAPH_MIN_CONFIDENCE = 0.7
_APP_ROOT = Path(__file__).resolve().parent.parent
_RESOURCE_DIR = _APP_ROOT / "resources"
_JIEBA_USER_DICT_PATH = _RESOURCE_DIR / "jieba_user_dict.txt"
_JIEBA_STOPWORDS_PATH = _RESOURCE_DIR / "jieba_stopwords.txt"
_DEFAULT_STOP_WORDS = {
    "的",
    "和",
    "是",
    "我",
    "在",
    "这个",
    "那个",
    "这里",
    "那边",
    "这边",
    "请问",
    "一下",
    "麻烦",
    "帮忙",
    "您好",
    "老师",
}
_JIEBA_RESOURCES_LOADED = False

_WIKILINK_RE = re.compile(r"\[\[(分类|问答|概念)-([^\]]+)\]\]")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_SECTION_FAQ_RE = re.compile(
    r"^###\s+\d+\.\s+(.+?)\s*$[\s\S]*?"
    r">\s*详情页：\[\[(问答-[^\]]+)\]\]",
    re.MULTILINE,
)
_STANDARD_ANSWER_RE = re.compile(r"##\s*标准回答\s*\n+([\s\S]*?)(?=\n##\s|\Z)")
_SUPPORT_PHRASE_RE = re.compile(
    r"不存在|未找到|失败|报错|异常|超时|无效|错误|失效|限流|拒绝|权限|过期"
)

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


def text_matches_keywords(text: str, keywords: list[str]) -> bool:
    return score_text(keywords, text) > 0


def find_relevant_pages_from_index(keywords: list[str], index_content: str, wiki_root: Path) -> list[Path]:
    """按关键词匹配 index 标题/高频词并映射到分类或概念页面。"""
    relevant: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        if path.is_file() and path not in seen:
            seen.add(path)
            relevant.append(path)

    for title, href in _MD_LINK_RE.findall(index_content):
        if not text_matches_keywords(title, keywords):
            continue
        rel_href = href.replace("\\", "/").lstrip("/")
        if rel_href in {"index.md", "overview.md"}:
            continue
        if rel_href.startswith("categories/") or rel_href.startswith("concepts/"):
            add((wiki_root / rel_href).resolve())

    blocks = re.split(r"\n###\s+", index_content)
    for block in blocks[1:]:
        lines = block.strip().splitlines()
        if not lines:
            continue
        section_title = lines[0].strip()
        block_matched = text_matches_keywords(section_title, keywords)
        if not block_matched:
            for line in lines[1:]:
                if line.strip().startswith("- ") and text_matches_keywords(line.strip()[2:], keywords):
                    block_matched = True
                    break
        if not block_matched:
            continue
        for line in lines[1:]:
            link_match = _WIKILINK_RE.search(line)
            if not link_match:
                continue
            prefix = link_match.group(1)
            if prefix == "分类":
                wikilink = f"分类-{link_match.group(2)}"
                cat_path = resolve_wiki_file(wiki_root, "categories", wikilink)
                if cat_path is not None:
                    add(cat_path)
            elif prefix == "概念":
                wikilink = f"概念-{link_match.group(2)}"
                concept_path = resolve_wiki_file(wiki_root, "concepts", wikilink)
                if concept_path is not None:
                    add(concept_path)

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
    """Extract keywords with mixed jieba + regex strategy."""
    text = question.strip()
    if not text:
        return []

    seen: set[str] = set()
    tokens: list[str] = []
    stop_words = load_stop_words()
    ensure_jieba_resources_loaded()

    def add(token: str) -> None:
        token = token.strip().lower()
        if len(token) < 2 or token in seen or token in stop_words:
            return
        seen.add(token)
        tokens.append(token)

    for match in re.finditer(r"[\u4e00-\u9fff]{2,}", text):
        segment = match.group()
        if jieba is not None:
            for token in jieba.cut_for_search(segment, HMM=True):
                add(token)
            for token in jieba.lcut(segment, HMM=True):
                add(token)
            for phrase in _SUPPORT_PHRASE_RE.findall(segment):
                add(phrase)
            continue

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


def ensure_jieba_resources_loaded() -> None:
    global _JIEBA_RESOURCES_LOADED

    if jieba is None or _JIEBA_RESOURCES_LOADED:
        return
    if _JIEBA_USER_DICT_PATH.is_file():
        jieba.load_userdict(str(_JIEBA_USER_DICT_PATH))
    _JIEBA_RESOURCES_LOADED = True


@lru_cache(maxsize=1)
def load_stop_words() -> set[str]:
    words = {word.strip().lower() for word in _DEFAULT_STOP_WORDS if word.strip()}
    if _JIEBA_STOPWORDS_PATH.is_file():
        for line in _JIEBA_STOPWORDS_PATH.read_text(encoding="utf-8").splitlines():
            word = line.strip().lower()
            if not word or word.startswith("#"):
                continue
            words.add(word)
    return words


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


def extract_wiki_links(markdown: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for match in _WIKILINK_RE.finditer(markdown):
        link_name = f"{match.group(1)}-{match.group(2)}".split("（")[0].strip()
        if link_name and link_name not in seen:
            seen.add(link_name)
            links.append(link_name)
    return links


def gather_related_links(root: Path, start_paths: list[Path], max_depth: int = 2) -> dict[str, set[Path]]:
    """从分类/概念页面递归提取链接，返回分类/概念/FAQ 关联集合。"""
    related: dict[str, set[Path]] = {"category": set(), "concept": set(), "faq": set()}
    queue: list[tuple[Path, int]] = [(path, 0) for path in start_paths]
    visited: set[Path] = set()

    while queue:
        path, depth = queue.pop(0)
        if path in visited or not path.is_file():
            continue
        visited.add(path)

        hit_type = _hit_type_for_path(path, root)
        if hit_type in ("category", "concept", "faq"):
            related[hit_type].add(path)

        if depth >= max_depth:
            continue

        text = read_text(path, max_chars=20000)
        for link_name in extract_wiki_links(text):
            if link_name.startswith("分类-"):
                linked = resolve_wiki_file(root, "categories", link_name)
            elif link_name.startswith("概念-"):
                linked = resolve_wiki_file(root, "concepts", link_name)
            elif link_name.startswith("问答-"):
                linked = resolve_wiki_file(root, "faqs", link_name)
            else:
                linked = None
            if linked is not None and linked not in visited:
                queue.append((linked, depth + 1))
    return related


def compute_hit_confidence(
    *,
    keywords: list[str],
    title: str,
    full_text: str,
    source_route: str,
    hit_type: str,
    link_boost: float = 0.0,
) -> float:
    """把关键词命中转换为 0~1 置信度，并叠加路由加权。"""
    if not keywords:
        return 0.0
    title_score = score_text(keywords, title)
    content_score = score_text(keywords, full_text)
    base = (title_score * 2.5 + content_score) / (len(keywords) * 3.5)
    route_bonus = {
        "index_exact": 0.25,
        "global_scan": 0.10,
        "link_traversal": 0.15,
        "index_llm_select": 0.12,
    }.get(source_route, 0.0)
    type_bonus = {
        "category": 0.20,
        "concept": 0.18,
        "faq": 0.05,
        "overview": -0.30,
        "page": 0.0,
    }.get(hit_type, 0.0)
    confidence = base + route_bonus + type_bonus + link_boost
    return max(0.0, min(1.0, confidence))


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
        score=compute_hit_confidence(
            keywords=keywords,
            title=title,
            full_text=full_text,
            source_route=source_route,
            hit_type=hit_type,
        ),
        hit_type=hit_type,
        source_route=source_route,
    )


def _build_sorted_buckets(
    hits: list[RetrievalHit],
    *,
    max_categories: int,
    max_faqs: int,
    max_concepts: int,
) -> tuple[list[RetrievalHit], list[RetrievalHit], list[RetrievalHit]]:
    category_hits = sorted(
        [h for h in hits if h.hit_type in ("category", "overview", "page")], key=lambda item: item.score, reverse=True
    )[:max_categories]
    faq_hits = sorted([h for h in hits if h.hit_type == "faq"], key=lambda item: item.score, reverse=True)[:max_faqs]
    concept_hits = sorted([h for h in hits if h.hit_type == "concept"], key=lambda item: item.score, reverse=True)[
        :max_concepts
    ]
    return category_hits, faq_hits, concept_hits


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
    """客服 wiki 混合检索主入口（关键词驱动版）。

    当前检索流程：
    1) 读取 `index.md`，仅从用户问题中抽取关键词（keywords）作为后续匹配依据；
    2) 先按关键词匹配 `index.md` 的标题/条目，映射出分类或概念文件；
       - 若 index 命中：只保留分类/概念作为种子，不回退全库扫描；
       - 若 index 未命中：全量扫描 `categories/*.md` 与 `concepts/*.md`；
    3) 以种子分类/概念递归提取 wiki 链接，扩展相关分类/概念，并最终拿到 FAQ 文件；
    4) 候选文件统一计算置信度（分类/概念权重高于 FAQ），按分数排序后截断；
    5) 若规则检索后仍无结果，且配置了 `index_page_selector`，使用 LLM 从 `index.md` 选页兜底。

    注意：
    - `index.md` 仅作为路由和选页依据，不作为答案内容来源；
    - 评分全程基于关键词命中，而不是直接使用原始问题文本做匹配。
    """
    root = Path(wiki_root)
    index_path = root / "index.md"
    notes: list[str] = []

    if not index_path.is_file():
        return WikiRetrievalResult(question=question, categories=[], faqs=[], concepts=[], keywords=[], retrieval_notes=[])

    index_content = read_text(index_path)
    keywords = extract_keywords(question)
    all_hits_by_path: dict[str, RetrievalHit] = {}

    def add_hit(path: Path, route: str, *, preloaded_text: str | None = None, link_boost: float = 0.0) -> None:
        hit = path_to_hit(
            path,
            root,
            keywords=keywords,
            source_route=route,
            preloaded_text=preloaded_text,
        )
        if hit is None:
            return
        hit.score = compute_hit_confidence(
            keywords=keywords,
            title=hit.title,
            full_text=preloaded_text if preloaded_text is not None else read_text(path, max_chars=12000),
            source_route=route,
            hit_type=hit.hit_type,
            link_boost=link_boost,
        )
        existed = all_hits_by_path.get(hit.rel_path)
        if existed is None or hit.score > existed.score:
            all_hits_by_path[hit.rel_path] = hit

    # 阶段 A：优先 index 路由。命中后仅走命中的分类/概念链路。
    index_matches = find_relevant_pages_from_index(keywords, index_content, root)
    index_match_found = bool(index_matches)
    if index_match_found:
        notes.append("index_match")
        # 命中 index 后仅以分类/概念作为后续种子，不直接引入 FAQ，且不回退全库扫描。
        index_matches = [p for p in index_matches if _hit_type_for_path(p, root) in ("category", "concept")]
        if not index_matches:
            notes.append("index_seed_empty")
        for matched_path in index_matches:
            add_hit(matched_path, "index_exact")
    # 仅在 index 未命中时，才允许全量扫描分类/概念。
    if not index_match_found:
        notes.append("global_scan")
        for path in (root / "categories").glob("*.md"):
            add_hit(path, "global_scan")
        for path in (root / "concepts").glob("*.md"):
            add_hit(path, "global_scan")

    # 阶段 B：从分类/概念种子递归扩展链接，补齐关联分类/概念与 FAQ。
    seed_paths = [root / hit.rel_path for hit in all_hits_by_path.values() if hit.hit_type in ("category", "concept")]
    related = gather_related_links(root, seed_paths)
    for category_path in related["category"]:
        add_hit(category_path, "link_traversal", link_boost=0.05)
    for concept_path in related["concept"]:
        add_hit(concept_path, "link_traversal", link_boost=0.05)
    for faq_path in related["faq"]:
        add_hit(faq_path, "link_traversal", link_boost=0.10)

    # 阶段 C：统一置信度排序并按类型截断。
    top_hits = sorted(all_hits_by_path.values(), key=lambda item: item.score, reverse=True)[:max_pages]
    category_hits, faq_hits, concept_hits = _build_sorted_buckets(
        top_hits,
        max_categories=max_categories,
        max_faqs=max_faqs,
        max_concepts=max_concepts,
    )

    # 规则检索无命中时：LLM 兜底
    if not category_hits and not faq_hits and not concept_hits and index_page_selector is not None:
        notes.append("index_llm_select")
        fallback_hits: list[RetrievalHit] = []
        for pick in index_page_selector(question, index_content, root):
            fallback_hit = path_to_hit(
                pick.path,
                root,
                keywords=keywords,
                source_route="index_llm_select",
                preloaded_text=pick.content,
            )
            if fallback_hit is None:
                continue
            fallback_hit.score = compute_hit_confidence(
                keywords=keywords,
                title=fallback_hit.title,
                full_text=pick.content,
                source_route="index_llm_select",
                hit_type=fallback_hit.hit_type,
            )
            fallback_hits.append(fallback_hit)
        category_hits, faq_hits, concept_hits = _build_sorted_buckets(
            sorted(fallback_hits, key=lambda item: item.score, reverse=True)[:max_pages],
            max_categories=max_categories,
            max_faqs=max_faqs,
            max_concepts=max_concepts,
        )

    return WikiRetrievalResult(
        question=question,
        categories=category_hits,
        faqs=faq_hits,
        concepts=concept_hits,
        keywords=keywords,
        retrieval_notes=notes,
    )
