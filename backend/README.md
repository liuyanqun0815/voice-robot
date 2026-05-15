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

## 核心模块

| 路径 | 职责 |
|------|------|
| `app/ws/voice_endpoint.py` | WebSocket 入口，VAD/音频/提交编排 |
| `app/services/asr/tencent_ws_client.py` | 腾讯实时 ASR |
| `app/services/agents/deepagent_runner.py` | DeepAgent + Ark 流式（`stream_mode=messages`） |
| `app/services/orchestrator.py` | 标点切段 → TTS 流式下发 |
| `app/services/tts/volcano_ws_client.py` | 火山 TTS v3 |

## 测试

```bash
python -m pytest tests/ -q
```
