import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.services.wiki_query import (
    CategoryEntry,
    has_category_relation_match,
    retrieve_kefu_wiki,
    select_index_pages_via_llm,
    should_use_index_llm_select,
)

_BACKEND = Path(__file__).resolve().parents[2]
_WIKI_ROOT = _BACKEND / "kefu-know" / "wiki"


def test_should_use_index_llm_when_no_category_relation() -> None:
    categories_meta = [
        CategoryEntry(
            title="科学软件与许可证",
            overview="",
            wikilink="分类-科学软件",
            high_freq_questions=[],
        )
    ]
    assert should_use_index_llm_select(
        "openclaw使用期限",
        ["openclaw", "期限"],
        categories_meta,
        ordered_paths=[],
        path_routes={},
    )


def test_has_category_relation_when_index_matched() -> None:
    cat_path = _WIKI_ROOT / "categories" / "分类-账户充值与计费.md"
    if not cat_path.is_file():
        pytest.skip("wiki sample missing")
    assert has_category_relation_match(
        "试用期限",
        ["试用", "期限"],
        [],
        ordered_paths=[cat_path],
        path_routes={cat_path: "index_match"},
    )


def test_select_index_pages_via_llm_parses_json(tmp_path: Path) -> None:
    wiki_root = tmp_path / "wiki"
    (wiki_root / "categories").mkdir(parents=True)
    (wiki_root / "faqs").mkdir(parents=True)
    cat_file = wiki_root / "categories" / "分类-测试.md"
    faq_file = wiki_root / "faqs" / "问答-测试.md"
    cat_file.write_text("# 测试", encoding="utf-8")
    faq_file.write_text("# 测试", encoding="utf-8")

    def fake_invoke(_prompt: str) -> str:
        return json.dumps(
            ["categories/分类-测试.md", "faqs/问答-测试.md", "missing/not-exist.md"],
            ensure_ascii=False,
        )

    resolved = select_index_pages_via_llm(
        "测试问题",
        "## 主题\n",
        wiki_root,
        invoke_llm=fake_invoke,
        max_pages=5,
    )
    assert len(resolved) == 2
    assert resolved[0].path == cat_file
    assert "测试" in resolved[0].content


@pytest.mark.skipif(not (_WIKI_ROOT / "index.md").is_file(), reason="kefu-know wiki not present")
def test_retrieve_uses_llm_selector_when_no_rule_match() -> None:
    from app.services.wiki_query import IndexPagePick, read_text

    def fake_selector(question: str, index_content: str, wiki_root: Path):
        faq = wiki_root / "faqs"
        candidates = sorted(faq.glob("*.md"))[:2]
        return [
            IndexPagePick(
                path=path,
                rel_path=str(path.relative_to(wiki_root)).replace("\\", "/"),
                content=read_text(path, max_chars=8000),
            )
            for path in candidates
        ]

    result = retrieve_kefu_wiki(
        "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
        _WIKI_ROOT,
        index_page_selector=fake_selector,
        max_faqs=0,
        max_categories=0,
        max_concepts=0,
    )
    assert "index_llm_select" in result.retrieval_notes
    assert result.faqs or result.categories
