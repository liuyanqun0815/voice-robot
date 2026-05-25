import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.core.request_context import bind_voice_context, get_wiki_audit, record_wiki_retrieval, reset_wiki_audit
from app.services.orchestrator import async_iter_sync_strings
from app.services.wiki_query import RetrievalHit, WikiRetrievalResult


def test_wiki_audit_visible_after_executor_stream() -> None:
    bind_voice_context(session_id="s-ctx", turn_id="t-ctx", trace_id="tr", input_mode="text")
    reset_wiki_audit()

    def sync_gen():
        record_wiki_retrieval(
            WikiRetrievalResult(
                question="q",
                categories=[],
                faqs=[
                    RetrievalHit(
                        rel_path="wiki/FAQ/x.md",
                        title="t",
                        excerpt="a",
                        score=1.0,
                        hit_type="faq",
                    )
                ],
                concepts=[],
                keywords=[],
                retrieval_notes=["keyword_faq"],
            )
        )
        yield "hello"

    async def consume():
        chunks = []
        async for item in async_iter_sync_strings(sync_gen()):
            chunks.append(item)
        return "".join(chunks)

    assert asyncio.run(consume()) == "hello"
    audit = get_wiki_audit()
    assert audit.tool_called is True
    assert audit.wiki_sources == ["wiki/FAQ/x.md"]
