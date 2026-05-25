import asyncio
import sys
import time
from pathlib import Path

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
