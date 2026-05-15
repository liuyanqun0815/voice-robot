# 实时语音机器人产品需求文档（PRD）

## 1. 文档信息

- **文档名称**：实时语音机器人产品需求文档
- **版本**：v1.0
- **文档类型**：生产级 PRD
- **关联文档**：
  - `docs/protocol.md`
  - `docs/qa-test-plan.md`

## 2. 背景与问题定义

文本对话系统可容忍更高响应延迟，语音对话对实时性、打断处理、噪声鲁棒性要求更高。  
本项目目标是在生产环境提供可连续多轮、可打断、可观测的语音机器人能力。

## 3. 产品目标

### 3.1 核心目标

- 提供从音频采集到语音播报的完整闭环能力
- 支持用户在 AI 说话过程中打断（Barge-in）
- 在双端点判停场景下保证同一轮仅触发一次推理
- 支持生产发布所需的监控、告警与验收标准

### 3.2 量化目标（SLO）

- 端到端首包延迟（用户停说到 AI 首段可播）：
  - P50 <= 900ms
  - P95 <= 1500ms
  - P99 <= 2500ms
- 打断生效时延（用户开口到旧播报停止）：P95 <= 300ms
- 同轮重复提交率：< 0.1%

## 4. 目标用户与典型场景

### 4.1 目标用户

- 使用语音交互的终端用户（客服、面试、问答场景）
- 业务运营、测试与运维团队

### 4.2 典型场景

- 用户语音提问，系统实时显示增量字幕并语音回复
- AI 播报中用户插话，系统立即停止旧回复并切入新轮
- 弱网/抖动情况下系统可恢复，不出现重复回复或状态错乱

## 5. 产品范围与非范围

### 5.1 本期范围（In Scope）

- 前端采集与前处理：AEC + NS + AGC
- 前端 VAD：`speech_start/speech_end`
- WebSocket 双向消息通道
- 服务端流式 ASR、LLM、句子级流式 TTS
- 播放队列、打断取消、会话状态回写

### 5.2 非范围（Out of Scope）

- 原生 Speech-to-Speech 单模型主链路
- 多模态（视频、屏幕共享）
- SIP 电话系统接入

## 6. 核心业务流程

1. 前端采集音频并做前处理（AEC/NS/AGC）
2. 前端按 100~200ms 分帧通过 WebSocket 持续上传
3. 服务端流式 ASR 输出 `partial/final` 文本
4. 回合结束信号由两路产生：
   - 前端 VAD 触发 `speech_end`
   - 服务端 ASR endpoint 触发结束
5. 服务端统一走 `commit_turn(turn_id)` 幂等提交
6. 服务端触发 LLM 推理并分句触发 TTS
7. 前端边收边播，最终以 `audio_complete` 收敛状态
8. 会话状态回写，进入下一轮

## 7. 关键产品能力需求

## 7.1 实时语音输入

- 必须支持麦克风权限获取失败提示
- 必须支持采集参数配置：
  - `echoCancellation=true`
  - `noiseSuppression=true`
  - `autoGainControl=true`
  - `sampleRate=16000`

## 7.2 增量字幕与提交策略

- 前端实时展示 ASR 增量文本
- 前端 `speech_end` 后增加 `200~400ms` 缓冲窗口，减少句尾截断
- 禁止“前端和服务端各自直接触发 LLM”，必须统一服务端幂等提交

## 7.3 打断能力（Barge-in）

- 用户插话时必须同时触发：
  - 停止前端播放队列
  - 取消服务端当前 `generation_id` 的 LLM/TTS
  - 清理未播报内容
- 仅已播放文本可写入会话历史

## 7.4 异常处理与恢复

- ASR 不可用时必须发送重连状态
- WebSocket 断开必须支持自动重连
- TTS 单句失败支持跳过并继续后续句

## 8. 状态机要求

### 8.1 会话状态

- `listening`
- `thinking`
- `speaking`
- `interrupted`
- `completed`

### 8.2 状态约束

- `listening -> thinking`：提交成功后进入
- `thinking -> speaking`：收到首个可播分片后进入
- `speaking -> interrupted -> listening`：用户打断路径
- 同一 `turn_id` 在任意状态下只允许一次成功提交

## 9. 非功能性需求

### 9.1 可观测性

- 必须记录：
  - `session_id`、`turn_id`、`trace_id`、`generation_id`
  - E2E 延迟、ASR 首字延迟、TTS 首包延迟
  - 打断次数、取消成功率、重复提交拒绝率

### 9.2 可靠性

- 支持服务端重试与超时控制
- 支持单会话故障隔离，不影响其他会话

### 9.3 安全性

- 通信使用 WSS（TLS）
- 日志中禁止输出原始敏感字段

## 10. 验收标准

### 10.1 功能验收

- 连续 5 轮对话无重复回复、无串话
- 双端点判停场景下只出现一次 `turn_committed`
- AI 播报中插话可在目标时延内生效

### 10.2 性能验收

- 达成第 3.2 节全部 SLO 指标

### 10.3 上线门禁

- P0/P1 缺陷为 0
- 核心用例通过率 >= 98%
- 与 `docs/qa-test-plan.md` 约定测试项全部通过

## 11. 里程碑建议

- **M1（链路打通）**：采集 -> ASR -> LLM -> TTS -> 播放
- **M2（体验完善）**：打断、幂等提交、重连恢复
- **M3（生产加固）**：可观测、告警、压测与灰度策略

## 12. 风险与修复建议

- **重复回复风险**：双端点并发触发提交  
  **修复建议**：`commit_turn(turn_id)` 原子幂等

- **句尾丢字风险**：`speech_end` 立即提交  
  **修复建议**：增加 `grace window`

- **打断不彻底风险**：只停播放器，不停服务端生成  
  **修复建议**：取消信号贯通到 LLM/TTS

## 13. 参考代码示例（Python）

```python
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class turn_state:
    turn_id: str
    committed: bool = False
    lock: Lock = field(default_factory=Lock)


def commit_turn_once(state: turn_state) -> bool:
    """同一轮只允许一次提交成功。"""
    with state.lock:
        if state.committed:
            return False
        state.committed = True
        return True
```
