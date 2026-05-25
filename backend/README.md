# voice-robot-backend

FastAPI 语音机器人后端。项目总览见 [../README.md](../README.md)。

## 包结构

- `app/` — 可安装应用包（`pip install -e ".[dev]"`）
- `vendor/tencentcloud-speech-sdk-python/` — 腾讯语音 SDK 源码，**不参与**打包

## 安装

```bash
cd backend
pip install -e ".[dev]"
```

## 启动

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

配置从 `backend/.env` 加载，模板见 `.env.example`。

开场白（WebSocket 建连后自动下发，不经 LLM）：

- `VOICE_ROBOT_GREETING_ENABLED=true`
- `VOICE_ROBOT_GREETING_TEXT=...`（多行用 `\n`）
- `VOICE_ROBOT_GREETING_TTS_ENABLED=true`（是否朗读）
- `VOICE_ROBOT_GREETING_STREAM_CHUNK_CHARS=2`、`VOICE_ROBOT_GREETING_STREAM_INTERVAL_MS=40`（流式打字机节奏）
- 开场白会在同 `session_id` 的**首轮 LLM 调用**中作为 `AIMessage` 注入 `messages` 输入；随后由 checkpointer 持久化，后续多轮 LLM 可见该上下文

### LangSmith 追踪（可选）

在 `.env` 中设置与 [LangChain 环境变量](https://docs.smith.langchain.com/) 一致的项（**不要**加 `VOICE_ROBOT_` 前缀）：

- `LANGCHAIN_TRACING_V2=true`
- `LANGCHAIN_PROJECT=<项目名>`
- `LANGSMITH_API_KEY=<密钥>`（或 `LANGCHAIN_API_KEY`）

应用启动时会在加载路由（及 LangChain）之前把这些值写入进程环境，便于在 LangSmith 中查看追踪。

## 核心模块

| 路径 | 职责 |
|------|------|
| `app/ws/voice_endpoint.py` | WebSocket 入口，VAD/音频/提交编排 |
| `app/services/asr/tencent_ws_client.py` | 腾讯实时 ASR |
| `app/services/agents/deepagent_runner.py` | DeepAgent + Ark 流式（`stream_mode=messages`） |
| `app/services/orchestrator.py` | 标点切段 → TTS 流式下发；建连后 `session_greeting` 开场白 |
| `app/services/tts/volcano_ws_client.py` | 火山 TTS v3 |

## 测试

```bash
python -m pytest tests/ -q
```
