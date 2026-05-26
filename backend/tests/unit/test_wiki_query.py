import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.services.wiki_query import (
    extract_keywords,
    find_relevant_pages_from_index,
    parse_index_categories,
    retrieve_kefu_wiki,
    title_matches_question,
)

_BACKEND = Path(__file__).resolve().parents[2]
_WIKI_ROOT = _BACKEND / "kefu-know" / "wiki"


@pytest.mark.skipif(not (_WIKI_ROOT / "index.md").is_file(), reason="kefu-know wiki not present")
def test_retrieve_trial_period_question() -> None:
    result = retrieve_kefu_wiki("算力平台试用期限多久？", _WIKI_ROOT)
    assert result.keywords
    assert result.categories or result.faqs
    text = result.to_prompt_text()
    assert "知识库检索结果" in text
    assert "试用" in text or "期限" in text or "openclaw" in text.lower()


@pytest.mark.skipif(not (_WIKI_ROOT / "index.md").is_file(), reason="kefu-know wiki not present")
def test_retrieve_faq_standard_answer_excerpt() -> None:
    result = retrieve_kefu_wiki("openclaw使用期限", _WIKI_ROOT, max_faqs=3)
    combined = result.to_prompt_text().lower()
    assert "标准回答" in combined or "tokens" in combined or "期限" in combined


def test_extract_keywords_chinese() -> None:
    keywords = extract_keywords("算力平台试用期限多久？")
    assert "试用" in keywords
    assert "期限" in keywords


def test_extract_keywords_mixed_mode_avoids_noisy_cjk_bigrams() -> None:
    keywords = extract_keywords("422 Model Not Exist 错误，模型不存在，如何解决？")

    assert "model" in keywords
    assert "exist" in keywords
    assert "模型" in keywords
    assert "不存在" in keywords
    assert "型不" not in keywords


def test_extract_keywords_uses_custom_dict_and_stopwords() -> None:
    keywords = extract_keywords("请问一下，这个核心节点邀测计划怎么参加？")

    assert "核心节点" in keywords
    assert "邀测计划" in keywords
    assert "请问" not in keywords
    assert "一下" not in keywords
    assert "这个" not in keywords


def test_title_matches_question_cjk_bigram() -> None:
    assert title_matches_question("账户充值与计费", "试用期限和计费")
    assert not title_matches_question("科学软件与许可证", "openclaw使用期限")


@pytest.mark.skipif(not (_WIKI_ROOT / "index.md").is_file(), reason="kefu-know wiki not present")
def test_find_relevant_pages_from_index_billing() -> None:
    index_text = (_WIKI_ROOT / "index.md").read_text(encoding="utf-8")
    paths = find_relevant_pages_from_index("试用期间的扣费是如何计算的", index_text, _WIKI_ROOT)
    assert paths
    assert any("账户充值" in p.stem or "计费" in p.stem for p in paths)


def test_parse_index_categories_reads_wikilinks() -> None:
    sample = """
## 主题分类

### 账户充值与计费（订单 / 发票 / Token 购买）

涵盖账户余额、充值缴费。

- 主题页：[[分类-账户充值与计费-订单-发票-Token-购买]]
- 高频问题：
  - 试用期间的扣费是如何计算的？
"""
    categories = parse_index_categories(sample)
    assert len(categories) == 1
    assert categories[0].wikilink.startswith("分类-")
    assert "试用" in categories[0].high_freq_questions[0]
