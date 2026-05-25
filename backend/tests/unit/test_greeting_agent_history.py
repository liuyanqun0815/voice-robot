import sys
from pathlib import Path

from langchain_core.messages import AIMessage, AIMessageChunk

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.core.settings import Settings
from app.services.agents.deepagent_runner import DeepAgentRunner


class _FakeAgent:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def stream(self, payload: dict, **_: object):
        self.calls.append(payload)
        yield {"type": "messages", "data": (AIMessageChunk(content="答"), {})}


def test_first_turn_injects_greeting_into_stream_input() -> None:
    runner = DeepAgentRunner(
        settings=Settings(
            greeting_enabled=True,
            greeting_text="您好，欢迎咨询。",
            mock_streaming_enabled=False,
        )
    )
    fake = _FakeAgent()
    runner._agent = fake

    chunks = list(runner.stream_assistant_text("一直显示启动中", thread_id="session-a"))

    assert "".join(chunks) == "答"
    assert len(fake.calls) == 1
    messages = fake.calls[0]["messages"]
    assert len(messages) == 2
    assert isinstance(messages[0], AIMessage)
    assert messages[0].content == "您好，欢迎咨询。"
    assert messages[1] == {"role": "user", "content": "一直显示启动中"}


def test_same_thread_only_injects_greeting_once() -> None:
    runner = DeepAgentRunner(
        settings=Settings(greeting_enabled=True, greeting_text="同一句", mock_streaming_enabled=False)
    )
    fake = _FakeAgent()
    runner._agent = fake

    list(runner.stream_assistant_text("第一个问题", thread_id="session-b"))
    list(runner.stream_assistant_text("第二个问题", thread_id="session-b"))

    assert len(fake.calls) == 2
    first_messages = fake.calls[0]["messages"]
    second_messages = fake.calls[1]["messages"]
    assert len(first_messages) == 2
    assert isinstance(first_messages[0], AIMessage)
    assert first_messages[0].content == "同一句"
    assert second_messages == [{"role": "user", "content": "第二个问题"}]
