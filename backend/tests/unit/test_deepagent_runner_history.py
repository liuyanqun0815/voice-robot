import sys
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES, RemoveMessage

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.core.settings import Settings
from app.services.agents.deepagent_runner import DeepAgentRunner


class _FakeStateAgent:
    def __init__(self, messages: list | None = None) -> None:
        self.messages = messages or []
        self.update_calls: list[tuple[dict, dict, str | None]] = []

    def get_state(self, config: dict):
        return SimpleNamespace(values={"messages": list(self.messages)})

    def update_state(self, config: dict, values: dict, as_node: str | None = None) -> None:
        self.update_calls.append((config, values, as_node))


def test_get_thread_messages_returns_seeded_greeting_when_checkpoint_empty() -> None:
    runner = DeepAgentRunner(
        Settings(greeting_enabled=True, greeting_text="您好，欢迎咨询。", mock_streaming_enabled=False)
    )
    fake = _FakeStateAgent()
    runner._agent = fake
    runner._greeting_seeded_threads.add("session-1")

    messages = runner.get_thread_messages("session-1")

    assert len(messages) == 1
    assert isinstance(messages[0], AIMessage)
    assert messages[0].content == "您好，欢迎咨询。"


def test_replace_thread_messages_uses_remove_all_then_rewrites() -> None:
    runner = DeepAgentRunner(Settings(mock_streaming_enabled=False))
    fake = _FakeStateAgent()
    runner._agent = fake

    new_messages = [HumanMessage(content="用户问题"), AIMessage(content="已输出部分")]

    runner.replace_thread_messages("session-2", new_messages)

    assert len(fake.update_calls) == 1
    config, values, as_node = fake.update_calls[0]
    assert config == {"configurable": {"thread_id": "session-2"}}
    assert as_node == "model"
    payload = values["messages"]
    assert isinstance(payload[0], RemoveMessage)
    assert payload[0].id == REMOVE_ALL_MESSAGES
    assert payload[1:] == new_messages
