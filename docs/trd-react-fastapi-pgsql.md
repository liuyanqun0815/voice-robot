# 实时语音机器人技术实现文档（TRD）

## 1. 文档目标

- 基于既有 `PRD/协议/测试`，给出可直接开发落地的技术方案。
- 技术栈固定为：
  - 前端：React
  - 后端：FastAPI
  - 数据库：PostgreSQL
  - 智能体执行：DeepAgent + LangChain
  - ASR：腾讯实时语音识别（WebSocket）
  - TTS：火山流式双工 TTS（`wss://openspeech.bytedance.com/api/v3/tts/bidirection`）

关联文档：
- `docs/prd.md`
- `docs/protocol.md`
- `docs/qa-test-plan.md`

## 2. 总体架构

```text
React(Web) ──WS──> FastAPI Orchestrator ──WS──> Tencent Realtime ASR
     ^                     |                         |
     |                     |                         v
     |                     |                    ASR partial/final
     |                     v
     |              DeepAgent LangChain Runner
     |                     |
     |                     v
     |              Volcano TTS Adapter ──WSS──> Bytedance TTS
     |                     |
     └─────audio_chunk <───┘
```

核心原则：
- 双端点判停（前端 VAD + 服务端 ASR endpoint）
- 单提交幂等（`commit_turn_once`）
- 可取消（`generation_id` 级别取消）

## 3. 模块拆分

## 3.1 前端（React）

模块建议：
- `AudioCapture`：采集 + AEC/NS/AGC + PCM 分帧
- `VadController`：本地 VAD，输出 `speech_start/speech_end`
- `WsClient`：统一收发协议消息
- `SubtitleStore`：字幕状态（partial/final）
- `PlaybackQueue`：TTS 分片播放队列
- `SessionStateStore`：`listening/thinking/speaking/interrupted`

关键要求：
- 采样率 16kHz，单声道，16bit PCM
- 分帧 100~200ms（建议 100ms 起）
- `speech_end` 后 200~400ms grace window

## 3.2 后端（FastAPI）

模块建议：
- `ws_gateway.py`：浏览器连接管理、事件分发
- `session_manager.py`：会话状态机
- `turn_manager.py`：回合幂等与取消
- `asr/tencent_asr_client.py`：腾讯 ASR WebSocket 客户端
- `agents/deepagent_runner.py`：DeepAgent LangChain 智能体执行
- `tts/volcano_tts_client.py`：火山双工 TTS 客户端
- `repositories/`：PostgreSQL 读写
- `metrics/`：指标与 tracing

## 3.3 数据层（PostgreSQL）

目标：
- 会话元数据持久化
- 对话轮次与消息持久化
- 事件审计与排障

## 4. 会话与回合状态机

会话状态：
- `listening`
- `thinking`
- `speaking`
- `interrupted`
- `completed`

回合规则：
- 一个 `turn_id` 只允许一次提交成功
- 新一轮开始必须生成新的 `turn_id`
- `cancel` 只对匹配 `generation_id` 生效

## 5. 协议实现约束

以 `docs/protocol.md` 为准，新增约束：
- 服务端必须校验 `seq` 单调递增（同一 `turn_id`）
- `turn_commit_request` 允许多次到达，但只有第一次成功
- `asr_final` 到达不自动触发推理，仍需显式 `commit`

## 6. 第三方服务接入设计

## 6.1 腾讯实时 ASR（WebSocket）

适配层职责：
- 建立每会话独立 ASR WebSocket 连接
- 持续发送音频帧
- 监听增量识别回调并映射为：
  - `asr_partial`
  - `asr_final`
- 连接异常时触发 `asr_reconnecting/asr_ready`

实现要点：
- 音频编码和采样参数与腾讯接口要求严格一致
- 建议在 `append_audio` 前做 ready 检查
- 设置读写超时，避免僵死连接

## 6.2 火山流式双工 TTS（WSS）

接入地址：
- `wss://openspeech.bytedance.com/api/v3/tts/bidirection`

适配层职责：
- 按句子边界送入文本
- 流式接收音频分片，转发前端 `tts_chunk`
- 句子结束或整轮结束后发送 `audio_complete`
- 支持 `generation_id` 级别取消

实现要点：
- 一轮内按 `chunk_index` 保序下发
- 单句失败可跳过，不能阻塞整轮
- 所有句子失败时返回错误并降级文本

## 7. 数据库模型（PostgreSQL）

```sql
create table if not exists voice_session (
    id bigserial primary key,
    session_id varchar(64) not null unique,
    user_id varchar(64),
    status varchar(32) not null,
    started_at timestamptz not null default now(),
    ended_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists voice_turn (
    id bigserial primary key,
    session_id varchar(64) not null,
    turn_id varchar(64) not null,
    generation_id varchar(64),
    commit_reason varchar(64),
    committed boolean not null default false,
    user_text text,
    ai_text_played text,
    ai_text_unplayed text,
    state varchar(32) not null,
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    unique(session_id, turn_id)
);

create table if not exists voice_event_log (
    id bigserial primary key,
    session_id varchar(64) not null,
    turn_id varchar(64),
    trace_id varchar(64) not null,
    event_type varchar(64) not null,
    payload jsonb not null,
    created_at timestamptz not null default now()
);
```

## 8. FastAPI 接口设计

## 8.1 WebSocket 入口

- 路径：`/ws/voice`
- 鉴权：JWT（query/header 二选一，建议 header）
- 连接后首个事件建议发送 `session_init`

## 8.2 健康检查

- `GET /healthz`：存活检查
- `GET /readyz`：依赖可用检查（DB、ASR、TTS、LLM）

## 8.3 回放与审计接口（可选）

- `GET /api/sessions/{session_id}`
- `GET /api/sessions/{session_id}/turns`

## 9. 关键实现代码示例

### 9.1 后端幂等提交（FastAPI 服务内）

```python
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional


@dataclass
class TurnState:
    turn_id: str
    committed: bool = False
    generation_id: Optional[str] = None
    lock: Lock = field(default_factory=Lock)


def commit_turn_once(state: TurnState) -> bool:
    with state.lock:
        if state.committed:
            return False
        state.committed = True
        state.generation_id = f"g_{state.turn_id}"
        return True
```

### 9.2 前端发起提交（React）

```typescript
function sendCommit(ws: WebSocket, sessionId: string, turnId: string, reason: string): void {
  ws.send(
    JSON.stringify({
      type: "turn_commit_request",
      session_id: sessionId,
      turn_id: turnId,
      reason,
      timestamp_ms: Date.now(),
    }),
  );
}
```

## 10. 配置项（`.env`）

```bash
# app
APP_ENV=prod
APP_PORT=8000
LOG_LEVEL=INFO

# db
PG_DSN=postgresql+psycopg://user:pass@127.0.0.1:5432/voice_robot

# tencent asr
TENCENT_ASR_APP_ID=xxx
TENCENT_ASR_SECRET_ID=xxx
TENCENT_ASR_SECRET_KEY=xxx
TENCENT_ASR_WS_URL=wss://...

# volcano tts
VOLCANO_TTS_APP_ID=xxx
VOLCANO_TTS_ACCESS_TOKEN=xxx
VOLCANO_TTS_WS_URL=wss://openspeech.bytedance.com/api/v3/tts/bidirection

# llm
LLM_BASE_URL=https://...
LLM_API_KEY=xxx
LLM_MODEL=xxx
```

## 11. 部署方案（生产建议）

- 运行方式：
  - FastAPI：`uvicorn` 多 worker（或 `gunicorn+uvicorn`）
  - 反向代理：Nginx（开启 WebSocket upgrade）
- 部署拓扑：
  - `frontend`、`api`、`postgres` 分离部署
  - `api` 无状态，横向扩展
- 必须开启：
  - 请求日志与结构化事件日志
  - tracing（`trace_id` 贯通）
  - 指标上报（Prometheus/OpenTelemetry）

## 12. 里程碑计划

- M1（1~2 周）：打通单轮链路（采集->ASR->LLM->TTS->播放）
- M2（1 周）：幂等提交、打断、取消语义
- M3（1 周）：数据库落库、可观测、重连策略
- M4（1 周）：压测与灰度上线

## 13. 验收清单（与 QA 文档联动）

- 功能：
  - 双端点判停场景无重复回复
  - 打断后旧流不再下发
- 性能：
  - E2E 首包达到 PRD SLO
  - 打断时延达标
- 稳定性：
  - 24h 稳定测试通过
  - ASR/TTS 间歇失败可恢复

## 14. 风险与修复建议

- **风险：ASR/TTS 供应商抖动导致时延波动**
  - 修复建议：超时阈值 + 重试退避 + 降级文本回显
- **风险：前后端重复提交**
  - 修复建议：服务端 `commit_turn_once` 原子幂等
- **风险：打断后旧音频残留播放**
  - 修复建议：播放队列清空并校验 `generation_id`

## 15. 官方接入参考

- 腾讯实时 ASR Python 示例：
  - https://github.com/TencentCloud/tencentcloud-speech-sdk-python/blob/master/examples/asr/asrexample.py
- 火山实时双工 TTS 文档：
  - https://www.volcengine.com/docs/6561/1329505?lang=zh
