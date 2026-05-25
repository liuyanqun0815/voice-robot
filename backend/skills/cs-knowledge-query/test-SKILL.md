---
name: cs-knowledge-query
description: Answers customer-service questions from the kefu wiki knowledge base (trial period, billing, API tokens, compute platform, refunds, account). Use for voice/chat Q&A, natural-language queries, or when the user asks about policies, usage limits, or product how-to. Read-only; does not ingest or rebuild wiki.
---

# 客服知识库查询（Query）

从已沉淀的 **kefu wiki** 检索标准答，供语音客服与自然语言问答使用。参考 [llm-wiki-agent](https://github.com/SamurAIGPT/llm-wiki-agent)：**先读索引定主题，再并行下钻，最后合并标准答并附来源**——非临时 RAG。

**与建库技能分工：** 数据采集、Excel/网站 ingest、lint、重建图谱见 `cs-knowledge-wiki`；本技能 **仅查询**。

## 知识库挂载路径（Agent 必读）

DeepAgent 文件后端虚拟路径（磁盘在 `backend/kefu-know/wiki/`）：

| 虚拟路径 | 内容 |
|----------|------|
| `/kefu-know/wiki/index.md` | 主题索引（分类名、概述、高频问题） |
| `/kefu-know/wiki/overview.md` | 跨主题总览 |
| `/kefu-know/wiki/categories/` | 主题页：该主题全部 FAQ 总结 + 流程 |
| `/kefu-know/wiki/faqs/` | 合并后的客户问答（标准回答 + 步骤） |
| `/kefu-know/wiki/concepts/` | 产品/术语概念（链回 FAQ） |
| `/kefu-know/wiki/syntheses/` | 历史 query 答案归档（可选写入） |

使用 `read_file`、`grep`、`ls` 访问上述路径；**禁止**用 `raw/` 替代 wiki 标准答（raw 仅作可选上下文校验）。

## 何时使用本技能

| 场景 | 示例 |
|------|------|
| 语音/聊天自然提问 | 「算力平台试用期限多久？」「退款多久到账？」 |
| 政策与口径 | 试用、计费、发票、账户、配额 |
| 产品与操作 | API Token、用量查询、作业提交、软件使用 |
| 显式前缀（可选） | `query: 退款需要多久到账？` |

**不必**要求用户说 `query:`；只要是在问客服知识库能回答的事实或流程，即走本技能。

## 执行顺序（优先工具，对齐 llm-wiki query.py）

**首选：** 调用内置工具 **`query_kefu_wiki(question)`**。程序侧流程（参考 [llm-wiki query.py](https://github.com/SamurAIGPT/llm-wiki-agent/blob/main/tools/query.py)）：

1. 读 `index.md`，用 **CJK 二字匹配** 命中 index 标题 / `[[分类-...]]`
2. 始终带上 `overview.md`（若有）
3. 若存在 `graph/graph.json`，沿 confidence≥0.7 的边 **扩展 1 跳邻居**
4. 命中过少且开启 `VOICE_ROBOT_WIKI_QUERY_LLM_FALLBACK_ENABLED` 时，**1 次 Fast LLM** 从 index 选路径
5. 关键词召回 FAQ / 概念，合并去重（最多约 15 页），FAQ 优先抽 **标准回答**

你只需根据工具返回内容合成 **1 次** 最终客服答复（适合语音）。

| 步骤 | 动作 | 禁止 |
|------|------|------|
| 1 | **调用** `query_kefu_wiki`，参数为用户原问题 | 未调工具就对 wiki 自行 read_file/grep |
| 2 | 阅读工具返回的主题页摘要、FAQ 标准答、概念摘录 | 编造工具未返回的口径 |
| 3 | 整合去重；冲突时采用更具体/更新的 FAQ | 忽略冲突 |
| 4 | 输出简洁中文答复，保留工具中的 `来源：...` 路径 | 无来源的断言 |

**兜底（仅当工具不可用）：** 按 index → categories → faqs → grep 手动检索（见下节）。

**可选：** 用户上下文含会话细节时，可读 `raw/chats/session_*.md` 作补充，**不得**用 raw 覆盖 wiki 标准口径。

### 手动检索（兜底）

| 步骤 | 动作 |
|------|------|
| 1 | 读 `/kefu-know/wiki/index.md`，选定 1～2 个 `[[分类-...]]` |
| 2 | 读对应 `categories/分类-*.md` |
| 3 | 读 `faqs/问答-*.md` 的「标准回答」 |
| 4 | 必要时 `grep` 补召回 |

## 示例：算力平台 / OpenClaw 试用期限

1. 读 `index.md` → 主题 **账户充值与计费** 和/或 **开放平台 API 与大模型 Token**（视问法）
2. 并行读对应 `categories/分类-*.md`
3. 并行读 `faqs/问答-*试用*`、`faqs/问答-*期限*`、`faqs/问答-*openclaw*` 等（以 grep 辅助定位文件名）
4. 并行 grep 关键词：`试用`、`期限`、`tokens`、`openclaw`
5. 整合 wiki 中的标准回答与步骤，语音友好复述，附 `[[问答-...]]` 来源

## 示例：tokens 统计表能否查看

1. `index.md` → **开放平台 API 与大模型 Token**（次选：账户充值与计费，仅当问购买/发票）
2. 并行读 `categories/分类-开放平台-API-与大模型-Token-Key-调用-余额.md`（以 index 实际链接为准）
3. 并行读 `faqs/问答-咨询下，tokens的统计表您这能看吗-*.md`、`faqs/问答-token用量在哪查询呢.md` 等
4. 并行 grep：`tokens`、`统计表`、`用量信息`
5. 整合：客服无后台查看权限 + 用户自助路径 + `[[来源页]]`

## 回复要求

- 先给 **结论**，再简短步骤；避免长列表朗读。
- 口径以 wiki **标准回答** 为准；wiki 无依据时明确「知识库未收录」，勿编造。
- 多条 FAQ 冲突时，说明差异并采用更新/更具体的条目。
- 每条关键结论尽量对应一个 `[[问答-...]]` 或 `[[分类-...]]` 来源。

## 实现说明

- 检索：`app/services/wiki_query.py`（`retrieve_kefu_wiki`）
- 工具名：**`query_kefu_wiki`**
- 可选图谱：`backend/kefu-know/graph/graph.json`（由 `build graph` 生成，无则跳过邻居扩展）
- 典型：**1 次工具 + 1～2 次 LLM 合成**；仅当开启 index LLM 兜底时，工具内多 **1 次** 选页 LLM

## 附加资源

- 建库、ingest、lint：`/skills/cs-knowledge-wiki/SKILL.md`
- 字段与清洗细则：`/skills/cs-knowledge-wiki/reference.md`
