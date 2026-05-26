import asyncio
import sys
import time
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.services.orchestrator import Orchestrator
from app.services.turn_manager import TurnManager


def _slow_char_stream() -> str:
    for ch in "你好，世界。":
        time.sleep(0.02)
        yield ch


def test_run_turn_stops_emitting_after_cancel() -> None:
    sink: list[dict] = []
    turn_manager = TurnManager()
    turn_manager.commit_turn_once("s1", "t1")
    generation_id = turn_manager.get_generation_id("s1", "t1")
    orchestrator = Orchestrator(send_event=sink.append, turn_manager=turn_manager)

    original_stream = orchestrator._agent_runner.stream_assistant_text

    def slow_stream(user_text: str, *, thread_id: str):
        yield from _slow_char_stream()

    orchestrator._agent_runner.stream_assistant_text = slow_stream  # type: ignore[method-assign]

    async def _run() -> str:
        task = asyncio.create_task(
            orchestrator.run_turn("s1", "t1", "测试", generation_id=generation_id),
        )
        await asyncio.sleep(0.03)
        turn_manager.cancel_generation("s1", "t1", generation_id)
        return await task

    outcome = asyncio.run(_run())
    orchestrator._agent_runner.stream_assistant_text = original_stream  # type: ignore[method-assign]

    assert outcome == "cancelled"
    assert not any(e["type"] == "audio_complete" for e in sink)
    llm_count = sum(1 for e in sink if e["type"] == "llm_delta")
    assert llm_count < len("你好，世界。")


def test_is_generation_active_after_cancel() -> None:
    manager = TurnManager()
    manager.commit_turn_once("s1", "t1")
    generation_id = manager.get_generation_id("s1", "t1")
    assert manager.is_generation_active("s1", "t1", generation_id) is True
    manager.cancel_generation("s1", "t1", generation_id)
    assert manager.is_generation_active("s1", "t1", generation_id) is False


class _FakeHistoryRunner:
    def __init__(self) -> None:
        self.replaced: tuple[str, list] | None = None
        self.closed = False

    def get_thread_messages(self, thread_id: str):
        return [AIMessage(content="开场白")]

    def stream_assistant_text(self, user_text: str, *, thread_id: str):
        yield "你"
        yield "好"
        time.sleep(0.2)
        yield "，"

    def replace_thread_messages(self, thread_id: str, messages: list) -> None:
        self.replaced = (thread_id, messages)

    def close_stream(self, stream_obj) -> None:
        self.closed = True
        close = getattr(stream_obj, "close", None)
        if callable(close):
            close()


def test_run_turn_persists_only_emitted_partial_history_on_cancel() -> None:
    sink: list[dict] = []
    turn_manager = TurnManager()
    turn_manager.commit_turn_once("s1", "t1")
    generation_id = turn_manager.get_generation_id("s1", "t1")
    orchestrator = Orchestrator(send_event=sink.append, turn_manager=turn_manager)
    fake_runner = _FakeHistoryRunner()
    orchestrator._agent_runner = fake_runner  # type: ignore[assignment]

    async def _run() -> str:
        task = asyncio.create_task(
            orchestrator.run_turn("s1", "t1", "测试问题", generation_id=generation_id),
        )
        for _ in range(100):
            if sum(1 for e in sink if e["type"] == "llm_delta") >= 2:
                break
            await asyncio.sleep(0.005)
        turn_manager.cancel_generation("s1", "t1", generation_id)
        return await task

    outcome = asyncio.run(_run())

    assert outcome == "cancelled"
    assert fake_runner.replaced is not None
    thread_id, messages = fake_runner.replaced
    assert thread_id == "s1"
    assert isinstance(messages[0], AIMessage)
    assert messages[0].content == "开场白"
    assert isinstance(messages[1], HumanMessage)
    assert messages[1].content == "测试问题"
    assert isinstance(messages[2], AIMessage)
    assert messages[2].content == "你好"
    assert fake_runner.closed is True


class _FakeNoDeltaHistoryRunner:
    def __init__(self) -> None:
        self.replaced: tuple[str, list] | None = None
        self.closed = False

    def get_thread_messages(self, thread_id: str):
        return [AIMessage(content="开场白")]

    def stream_assistant_text(self, user_text: str, *, thread_id: str):
        time.sleep(0.2)
        if False:
            yield ""

    def replace_thread_messages(self, thread_id: str, messages: list) -> None:
        self.replaced = (thread_id, messages)

    def close_stream(self, stream_obj) -> None:
        self.closed = True
        close = getattr(stream_obj, "close", None)
        if callable(close):
            close()


def test_run_turn_persists_user_message_when_cancelled_before_first_delta() -> None:
    sink: list[dict] = []
    turn_manager = TurnManager()
    turn_manager.commit_turn_once("s1", "t1")
    generation_id = turn_manager.get_generation_id("s1", "t1")
    orchestrator = Orchestrator(send_event=sink.append, turn_manager=turn_manager)
    fake_runner = _FakeNoDeltaHistoryRunner()
    orchestrator._agent_runner = fake_runner  # type: ignore[assignment]

    async def _run() -> str:
        task = asyncio.create_task(
            orchestrator.run_turn("s1", "t1", "第一条问题", generation_id=generation_id),
        )
        await asyncio.sleep(0.02)
        turn_manager.cancel_generation("s1", "t1", generation_id)
        return await task

    outcome = asyncio.run(_run())

    assert outcome == "cancelled"
    assert fake_runner.replaced is not None
    thread_id, messages = fake_runner.replaced
    assert thread_id == "s1"
    assert len(messages) == 2
    assert isinstance(messages[0], AIMessage)
    assert messages[0].content == "开场白"
    assert isinstance(messages[1], HumanMessage)
    assert messages[1].content == "第一条问题"
    assert fake_runner.closed is True
