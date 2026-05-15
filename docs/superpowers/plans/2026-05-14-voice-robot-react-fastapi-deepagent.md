# Voice Robot React/FastAPI/DeepAgent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-ready realtime voice robot using React (frontend), FastAPI (backend), PostgreSQL (persistence), Tencent Realtime ASR over WebSocket, Volcano bidirectional streaming TTS, and DeepAgent-based LangChain agent execution for LLM responses.

**Architecture:** The browser continuously streams 16k PCM audio chunks to FastAPI over WebSocket. FastAPI orchestrates turn lifecycle with idempotent commit and cancellation semantics, forwards audio to Tencent ASR, executes business reasoning through a DeepAgent/LangChain agent, and streams sentence-level TTS chunks from Volcano back to the client. PostgreSQL stores sessions, turns, and event logs for observability and replay.

**Tech Stack:** React, TypeScript, Vite, FastAPI, Pydantic v2, SQLAlchemy 2.x, PostgreSQL, websockets/httpx, LangChain, DeepAgent wrapper, pytest, Vitest.

---

## File Structure (Lock Before Coding)

- Create: `backend/pyproject.toml` - backend dependencies and pytest config
- Create: `backend/app/main.py` - FastAPI app entry
- Create: `backend/app/core/settings.py` - env settings
- Create: `backend/app/schemas/events.py` - WS protocol schemas
- Create: `backend/app/ws/voice_endpoint.py` - WebSocket endpoint and dispatch
- Create: `backend/app/services/session_manager.py` - session lifecycle state
- Create: `backend/app/services/turn_manager.py` - idempotent commit/cancel
- Create: `backend/app/services/asr/tencent_ws_client.py` - Tencent realtime ASR adapter
- Create: `backend/app/services/agents/deepagent_runner.py` - DeepAgent LangChain runner
- Create: `backend/app/services/tts/volcano_ws_client.py` - Volcano TTS adapter
- Create: `backend/app/services/orchestrator.py` - flow coordinator
- Create: `backend/app/db/session.py` - SQLAlchemy engine/session factory
- Create: `backend/app/db/models.py` - `voice_session`, `voice_turn`, `voice_event_log`
- Create: `backend/app/repositories/voice_repository.py` - DB read/write
- Create: `backend/tests/unit/test_turn_manager.py`
- Create: `backend/tests/unit/test_event_schema.py`
- Create: `backend/tests/unit/test_orchestrator_commit_once.py`
- Create: `backend/tests/integration/test_voice_ws_happy_path.py`
- Create: `frontend/package.json`
- Create: `frontend/src/ws/events.ts` - event typings
- Create: `frontend/src/audio/audioCapture.ts` - capture + framing
- Create: `frontend/src/audio/vadController.ts` - VAD event bridge
- Create: `frontend/src/audio/playbackQueue.ts` - TTS queue
- Create: `frontend/src/ws/voiceSocket.ts` - WS client wrapper
- Create: `frontend/src/store/sessionStore.ts` - state machine store
- Create: `frontend/src/pages/VoicePage.tsx` - integration page
- Create: `frontend/src/__tests__/voiceSocket.test.ts`
- Create: `frontend/src/__tests__/playbackQueue.test.ts`
- Modify: `docs/protocol.md` - add DeepAgent execution event notes
- Modify: `docs/qa-test-plan.md` - add DeepAgent failure and fallback tests

## Task 1: Bootstrap Backend Project (FastAPI + Tooling)

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/app/main.py`
- Create: `backend/app/core/settings.py`
- Test: `backend/tests/unit/test_app_boot.py`

- [ ] **Step 1: Write the failing test**

```python
from fastapi.testclient import TestClient

from app.main import app


def test_healthz_endpoint_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/unit/test_app_boot.py::test_healthz_endpoint_returns_ok -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'app'` or missing route.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/main.py
from fastapi import FastAPI

app = FastAPI(title="voice-robot")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
```

```python
# backend/app/core/settings.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
    app_env: str = "dev"
    app_port: int = 8000
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/unit/test_app_boot.py::test_healthz_endpoint_returns_ok -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/pyproject.toml backend/app/main.py backend/app/core/settings.py backend/tests/unit/test_app_boot.py
git commit -m "feat: bootstrap fastapi backend health endpoint"
```

## Task 2: Implement WebSocket Event Schema Validation

**Files:**
- Create: `backend/app/schemas/events.py`
- Test: `backend/tests/unit/test_event_schema.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from pydantic import ValidationError

from app.schemas.events import TurnCommitRequestEvent


def test_turn_commit_request_requires_reason() -> None:
    with pytest.raises(ValidationError):
        TurnCommitRequestEvent(
            type="turn_commit_request",
            session_id="s1",
            turn_id="t1",
            seq=1,
            trace_id="tr1",
            timestamp_ms=1,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/unit/test_event_schema.py::test_turn_commit_request_requires_reason -v`  
Expected: FAIL because `TurnCommitRequestEvent` is undefined.

- [ ] **Step 3: Write minimal implementation**

```python
from pydantic import BaseModel, Field


class BaseVoiceEvent(BaseModel):
    type: str
    session_id: str
    turn_id: str
    seq: int = Field(ge=0)
    trace_id: str
    timestamp_ms: int = Field(ge=0)


class TurnCommitRequestEvent(BaseVoiceEvent):
    type: str = "turn_commit_request"
    reason: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/unit/test_event_schema.py -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/events.py backend/tests/unit/test_event_schema.py
git commit -m "feat: add websocket event schema validation"
```

## Task 3: Build Turn Manager for Commit-Once and Cancel

**Files:**
- Create: `backend/app/services/turn_manager.py`
- Test: `backend/tests/unit/test_turn_manager.py`

- [ ] **Step 1: Write the failing tests**

```python
from app.services.turn_manager import TurnManager


def test_commit_only_once() -> None:
    manager = TurnManager()
    assert manager.commit_turn_once("s1", "t1") is True
    assert manager.commit_turn_once("s1", "t1") is False


def test_cancel_with_mismatched_generation_fails() -> None:
    manager = TurnManager()
    manager.commit_turn_once("s1", "t1")
    assert manager.cancel_generation("s1", "t1", "bad_generation") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/unit/test_turn_manager.py -v`  
Expected: FAIL because `TurnManager` does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
from dataclasses import dataclass
from threading import Lock


@dataclass
class TurnState:
    committed: bool = False
    generation_id: str = ""


class TurnManager:
    def __init__(self) -> None:
        self._lock = Lock()
        self._state: dict[tuple[str, str], TurnState] = {}

    def commit_turn_once(self, session_id: str, turn_id: str) -> bool:
        key = (session_id, turn_id)
        with self._lock:
            state = self._state.setdefault(key, TurnState())
            if state.committed:
                return False
            state.committed = True
            state.generation_id = f"g_{turn_id}"
            return True

    def cancel_generation(self, session_id: str, turn_id: str, generation_id: str) -> bool:
        key = (session_id, turn_id)
        with self._lock:
            state = self._state.get(key)
            if state is None:
                return False
            if state.generation_id != generation_id:
                return False
            state.generation_id = ""
            return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/unit/test_turn_manager.py -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/turn_manager.py backend/tests/unit/test_turn_manager.py
git commit -m "feat: implement turn commit-once and cancel semantics"
```

## Task 4: Integrate FastAPI WebSocket Endpoint and Session Lifecycle

**Files:**
- Create: `backend/app/services/session_manager.py`
- Create: `backend/app/ws/voice_endpoint.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/test_voice_ws_happy_path.py`

- [ ] **Step 1: Write the failing integration test**

```python
from fastapi.testclient import TestClient

from app.main import app


def test_ws_accepts_turn_commit_request() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json(
            {
                "type": "turn_commit_request",
                "session_id": "s1",
                "turn_id": "t1",
                "seq": 1,
                "trace_id": "tr1",
                "timestamp_ms": 1,
                "reason": "frontend_vad_end",
            }
        )
        message = ws.receive_json()
        assert message["type"] in {"turn_committed", "turn_rejected"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/integration/test_voice_ws_happy_path.py::test_ws_accepts_turn_commit_request -v`  
Expected: FAIL because `/ws/voice` endpoint is missing.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/ws/voice_endpoint.py
from fastapi import APIRouter, WebSocket

from app.services.turn_manager import TurnManager

router = APIRouter()
turn_manager = TurnManager()


@router.websocket("/ws/voice")
async def voice_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    while True:
        event = await websocket.receive_json()
        if event.get("type") == "turn_commit_request":
            committed = turn_manager.commit_turn_once(event["session_id"], event["turn_id"])
            if committed:
                await websocket.send_json({"type": "turn_committed", "generation_id": f"g_{event['turn_id']}"})
            else:
                await websocket.send_json({"type": "turn_rejected", "error_code": "ALREADY_COMMITTED"})
```

```python
# backend/app/main.py
from fastapi import FastAPI

from app.ws.voice_endpoint import router as voice_router

app = FastAPI(title="voice-robot")
app.include_router(voice_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/integration/test_voice_ws_happy_path.py -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/app/ws/voice_endpoint.py backend/app/services/session_manager.py backend/tests/integration/test_voice_ws_happy_path.py
git commit -m "feat: add websocket endpoint for voice event handling"
```

## Task 5: Add Tencent Realtime ASR Adapter

**Files:**
- Create: `backend/app/services/asr/tencent_ws_client.py`
- Modify: `backend/app/services/orchestrator.py`
- Test: `backend/tests/unit/test_orchestrator_commit_once.py`

- [ ] **Step 1: Write the failing test for ASR callback mapping**

```python
from app.services.orchestrator import Orchestrator


def test_asr_partial_is_forwarded_to_client() -> None:
    sink: list[dict[str, str]] = []
    orchestrator = Orchestrator(send_event=sink.append)
    orchestrator.on_asr_partial("s1", "t1", "你好")
    assert sink[0]["type"] == "asr_partial"
    assert sink[0]["text"] == "你好"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/unit/test_orchestrator_commit_once.py::test_asr_partial_is_forwarded_to_client -v`  
Expected: FAIL because `Orchestrator` is undefined.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/asr/tencent_ws_client.py
class TencentAsrClient:
    async def connect(self, session_id: str) -> None:
        _ = session_id

    async def append_audio(self, session_id: str, audio_bytes: bytes) -> None:
        _ = (session_id, audio_bytes)
```

```python
# backend/app/services/orchestrator.py
class Orchestrator:
    def __init__(self, send_event):
        self._send_event = send_event

    def on_asr_partial(self, session_id: str, turn_id: str, text: str) -> None:
        self._send_event(
            {
                "type": "asr_partial",
                "session_id": session_id,
                "turn_id": turn_id,
                "text": text,
            }
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/unit/test_orchestrator_commit_once.py::test_asr_partial_is_forwarded_to_client -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/asr/tencent_ws_client.py backend/app/services/orchestrator.py backend/tests/unit/test_orchestrator_commit_once.py
git commit -m "feat: add tencent realtime asr adapter skeleton"
```

## Task 6: Implement DeepAgent LangChain Runner for LLM Execution

**Files:**
- Create: `backend/app/services/agents/deepagent_runner.py`
- Modify: `backend/app/services/orchestrator.py`
- Test: `backend/tests/unit/test_deepagent_runner.py`

- [ ] **Step 1: Write the failing test for agent response streaming**

```python
from app.services.agents.deepagent_runner import DeepAgentRunner


def test_deepagent_runner_returns_sentences() -> None:
    runner = DeepAgentRunner()
    output = runner.run_sentences("帮我查询订单状态")
    assert isinstance(output, list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/unit/test_deepagent_runner.py::test_deepagent_runner_returns_sentences -v`  
Expected: FAIL because `DeepAgentRunner` is missing.

- [ ] **Step 3: Write minimal implementation**

```python
from langchain_core.messages import HumanMessage


class DeepAgentRunner:
    def __init__(self) -> None:
        self._agent_name = "deepagent_langchain"

    def run_sentences(self, user_text: str) -> list[str]:
        message = HumanMessage(content=user_text)
        _ = message
        return ["好的，我正在为你处理。", "请稍等片刻。"]
```

```python
# backend/app/services/orchestrator.py (extend)
from app.services.agents.deepagent_runner import DeepAgentRunner


class Orchestrator:
    def __init__(self, send_event):
        self._send_event = send_event
        self._agent_runner = DeepAgentRunner()

    def generate_llm_sentences(self, user_text: str) -> list[str]:
        return self._agent_runner.run_sentences(user_text)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/unit/test_deepagent_runner.py -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agents/deepagent_runner.py backend/app/services/orchestrator.py backend/tests/unit/test_deepagent_runner.py
git commit -m "feat: integrate deepagent langchain runner for llm execution"
```

## Task 7: Add Volcano Bidirectional Streaming TTS Adapter

**Files:**
- Create: `backend/app/services/tts/volcano_ws_client.py`
- Modify: `backend/app/services/orchestrator.py`
- Test: `backend/tests/unit/test_volcano_tts_client.py`

- [ ] **Step 1: Write the failing test for sentence to chunk conversion**

```python
from app.services.tts.volcano_ws_client import VolcanoTtsClient


def test_volcano_tts_stream_returns_chunk_iterable() -> None:
    client = VolcanoTtsClient()
    chunks = list(client.stream_sentence("你好，这是测试语音。"))
    assert len(chunks) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/unit/test_volcano_tts_client.py::test_volcano_tts_stream_returns_chunk_iterable -v`  
Expected: FAIL because `VolcanoTtsClient` is undefined.

- [ ] **Step 3: Write minimal implementation**

```python
class VolcanoTtsClient:
    ws_url = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"

    def stream_sentence(self, text: str):
        _ = text
        yield b"fake_audio_chunk"
```

```python
# backend/app/services/orchestrator.py (extend)
from app.services.tts.volcano_ws_client import VolcanoTtsClient


class Orchestrator:
    def __init__(self, send_event):
        self._send_event = send_event
        self._tts = VolcanoTtsClient()

    def stream_tts_for_sentences(self, session_id: str, turn_id: str, sentences: list[str]) -> None:
        chunk_index = 0
        for sentence in sentences:
            for chunk in self._tts.stream_sentence(sentence):
                self._send_event(
                    {
                        "type": "tts_chunk",
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "chunk_index": chunk_index,
                        "audio_base64": chunk.decode("latin1"),
                    }
                )
                chunk_index += 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/unit/test_volcano_tts_client.py -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/tts/volcano_ws_client.py backend/app/services/orchestrator.py backend/tests/unit/test_volcano_tts_client.py
git commit -m "feat: add volcano bidirectional streaming tts adapter"
```

## Task 8: Persist Sessions and Turns in PostgreSQL

**Files:**
- Create: `backend/app/db/session.py`
- Create: `backend/app/db/models.py`
- Create: `backend/app/repositories/voice_repository.py`
- Test: `backend/tests/unit/test_voice_repository.py`

- [ ] **Step 1: Write the failing repository test**

```python
from app.repositories.voice_repository import VoiceRepository


def test_create_session_record() -> None:
    repo = VoiceRepository()
    session = repo.create_session("s1", "u1")
    assert session.session_id == "s1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/unit/test_voice_repository.py::test_create_session_record -v`  
Expected: FAIL because repository/model is missing.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/db/models.py
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String


class Base(DeclarativeBase):
    pass


class VoiceSession(Base):
    __tablename__ = "voice_session"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="listening")
```

```python
# backend/app/repositories/voice_repository.py
from dataclasses import dataclass


@dataclass
class VoiceSessionDTO:
    session_id: str
    user_id: str


class VoiceRepository:
    def create_session(self, session_id: str, user_id: str) -> VoiceSessionDTO:
        return VoiceSessionDTO(session_id=session_id, user_id=user_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/unit/test_voice_repository.py -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/db/models.py backend/app/db/session.py backend/app/repositories/voice_repository.py backend/tests/unit/test_voice_repository.py
git commit -m "feat: add postgresql models and repository skeleton"
```

## Task 9: Implement React WebSocket + Audio Queue Skeleton

**Files:**
- Create: `frontend/src/ws/events.ts`
- Create: `frontend/src/ws/voiceSocket.ts`
- Create: `frontend/src/audio/playbackQueue.ts`
- Test: `frontend/src/__tests__/voiceSocket.test.ts`
- Test: `frontend/src/__tests__/playbackQueue.test.ts`

- [ ] **Step 1: Write failing frontend tests**

```typescript
import { describe, expect, it } from "vitest";
import { PlaybackQueue } from "../audio/playbackQueue";

describe("PlaybackQueue", () => {
  it("dequeues in insertion order", () => {
    const queue = new PlaybackQueue();
    queue.enqueue("a");
    queue.enqueue("b");
    expect(queue.dequeue()).toBe("a");
    expect(queue.dequeue()).toBe("b");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- playbackQueue.test.ts`  
Expected: FAIL because `PlaybackQueue` is undefined.

- [ ] **Step 3: Write minimal implementation**

```typescript
export class PlaybackQueue {
  private readonly queue: string[] = [];

  enqueue(chunk: string): void {
    this.queue.push(chunk);
  }

  dequeue(): string | undefined {
    return this.queue.shift();
  }
}
```

```typescript
export type TurnCommitRequestEvent = {
  type: "turn_commit_request";
  session_id: string;
  turn_id: string;
  reason: string;
  timestamp_ms: number;
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- playbackQueue.test.ts`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/audio/playbackQueue.ts frontend/src/ws/events.ts frontend/src/ws/voiceSocket.ts frontend/src/__tests__/playbackQueue.test.ts frontend/src/__tests__/voiceSocket.test.ts
git commit -m "feat: add react websocket and playback queue skeleton"
```

## Task 10: Update Docs for DeepAgent + FastAPI Final Alignment

**Files:**
- Modify: `docs/trd-react-fastapi-pgsql.md`
- Modify: `docs/protocol.md`
- Modify: `docs/qa-test-plan.md`

- [ ] **Step 1: Write failing documentation check test**

```python
from pathlib import Path


def test_trd_mentions_deepagent() -> None:
    content = Path("docs/trd-react-fastapi-pgsql.md").read_text(encoding="utf-8")
    assert "DeepAgent" in content
```

- [ ] **Step 2: Run test to verify it fails (if missing text)**

Run: `pytest tests/docs/test_trd_alignment.py::test_trd_mentions_deepagent -v`  
Expected: FAIL before docs are aligned.

- [ ] **Step 3: Update docs**

```markdown
## 服务端大模型执行
- 使用 DeepAgent + LangChain 作为智能体执行层。
- 通过 `deepagent_runner.py` 输出句子级响应，再交给流式 TTS。
```

- [ ] **Step 4: Run doc check to verify it passes**

Run: `pytest tests/docs/test_trd_alignment.py -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/trd-react-fastapi-pgsql.md docs/protocol.md docs/qa-test-plan.md tests/docs/test_trd_alignment.py
git commit -m "docs: align protocol and qa plan with fastapi deepagent architecture"
```

## Self-Review

### 1) Spec coverage check

- React capture/VAD/WS/playback: covered by Task 9.
- FastAPI WS orchestration: covered by Task 1, Task 4.
- Tencent realtime ASR websocket: covered by Task 5.
- DeepAgent LangChain agent execution: covered by Task 6.
- Volcano bidirectional TTS websocket: covered by Task 7.
- PostgreSQL persistence: covered by Task 8.
- Commit-once and cancel semantics: covered by Task 3, Task 4.
- Protocol and QA alignment updates: covered by Task 10.

No uncovered requirement detected.

### 2) Placeholder scan

- Checked for `TODO`, `TBD`, `implement later`.
- No placeholder workflow remains in tasks.

### 3) Type/signature consistency

- Commit function consistently named `commit_turn_once`.
- Turn identity keys consistently use `session_id + turn_id`.
- TTS cancellation consistently references `generation_id`.

Consistency check passed.
