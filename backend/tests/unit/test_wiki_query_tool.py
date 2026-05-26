import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.core.settings import Settings
from app.services.agents.wiki_query_tool import build_query_kefu_wiki_tool


def test_query_kefu_wiki_tool_description_guides_question_enrichment(tmp_path: Path) -> None:
    tool = build_query_kefu_wiki_tool(tmp_path / "wiki", settings=Settings())

    description = tool.description

    assert "尽可能完整表达客户的问题" in description
    assert "必要时补充主语、谓语、对象或场景" in description
    assert "不要编造客户未提供的关键事实" in description
