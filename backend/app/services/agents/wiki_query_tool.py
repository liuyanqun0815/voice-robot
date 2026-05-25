"""LangChain tool wrapping kefu wiki retrieval (llm-wiki-agent query.py aligned)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from langchain.tools import tool

from app.core.metrics import WIKI_RETRIEVAL_NOTES_TOTAL, WIKI_TOOL_CALLS_TOTAL
from app.core.request_context import record_wiki_retrieval
from app.core.settings import Settings
from app.services.wiki_query import IndexPageSelector, retrieve_kefu_wiki, select_index_pages_via_llm

logger = logging.getLogger(__name__)


def build_ark_index_page_selector(settings: Settings) -> IndexPageSelector | None:
    """Fast Ark call to pick wiki paths from index when keyword match is weak."""
    if not settings.wiki_query_llm_fallback_enabled:
        return None
    if not settings.deepagent_ark_api_key.get_secret_value():
        return None

    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        return None

    llm = ChatOpenAI(
        model=settings.deepagent_ark_model,
        api_key=settings.deepagent_ark_api_key.get_secret_value(),
        base_url=settings.deepagent_ark_base_url,
        temperature=0.0,
        timeout=settings.deepagent_timeout_seconds,
        max_tokens=512,
    )

    def invoke_llm(prompt: str) -> str:
        response = llm.invoke(prompt)
        content = response.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif isinstance(block, str):
                    parts.append(block)
            return "".join(parts)
        return str(content)

    def selector(question: str, index_content: str, wiki_root: Path):
        logger.info("wiki query: index LLM select for question=%s", question[:80])
        return select_index_pages_via_llm(
            question,
            index_content,
            wiki_root,
            invoke_llm=invoke_llm,
            max_pages=settings.wiki_query_index_llm_max_pages,
        )

    return selector


def build_query_kefu_wiki_tool(
    wiki_root: Path,
    *,
    repo_root: Path | None = None,
    settings: Settings | None = None,
) -> Callable[[str], str]:
    """Bind wiki root and return a LangChain tool callable."""
    resolved_settings = settings or Settings()
    index_selector = build_ark_index_page_selector(resolved_settings)
    kefu_root = repo_root or wiki_root.parent

    @tool
    def query_kefu_wiki(question: str) -> str:
        """检索客服知识库 wiki，返回主题页、FAQ 标准回答与来源路径。

        检索流程：index 标题/分类匹配 → graph 邻居 → 关键词 FAQ；
        若未匹配到相关分类关系，则从 index.md 由大模型选最相关 n 页并载入正文（需 VOICE_ROBOT_WIKI_QUERY_LLM_FALLBACK_ENABLED）。
        请仅根据返回内容用简洁中文作答，并保留来源路径；无依据时说明知识库未收录。
        """
        result = retrieve_kefu_wiki(
            question,
            wiki_root,
            repo_root=kefu_root,
            index_page_selector=index_selector,
        )
        record_wiki_retrieval(result)
        WIKI_TOOL_CALLS_TOTAL.labels(tool="query_kefu_wiki").inc()
        for note in result.retrieval_notes:
            WIKI_RETRIEVAL_NOTES_TOTAL.labels(note=note).inc()
        return result.to_prompt_text()

    return query_kefu_wiki
