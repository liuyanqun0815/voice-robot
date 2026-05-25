# 智能客服知识库 — 参考手册

## 网站抓取

### 范围

- 默认：同域名 BFS，深度 `--max-depth`（默认 3）
- 遵守 `robots.txt`；`--delay` 默认 1s
- 排除：`*.pdf`、`/login`、`/logout`、带 `?` 的追踪参数（可 `--allow-query`）
- 重抓加 `--clean`：清空输出目录旧 `.md` 后再写入

### 编码与正文

- 响应按 `Content-Type` / `utf-8` / `gb18030` 解码，避免中文乱码
- 对已乱码文本尝试 `repair_mojibake()`（Latin-1 误读 UTF-8）
- 正文提取：`trafilatura` → `article` / `main` / `.content` 回退
- 清洗：去零宽字符、重复标题、多余空行

### 文件名（体现核心内容）

- 规则：`{页面标题}-{首个h2}.md`；无 h2 时用标题；无标题时用 URL 路径语义片段
- 示例：`开始使用-调用模型API.md`、`CodingPlan-错误码.md`
- 重名时追加 6 位哈希后缀

### raw 输出 frontmatter

```yaml
---
source_type: website
source_url: https://docs.example.com/guide
fetched_at: 2026-05-19T10:00:00
title: 操作指南
filename_core: 操作指南-快速入门
---
```

---

## Agent 萃取总则（网站 + 会话）

**禁止**在 ingest 脚本内调用 LLM API；**禁止**仅凭队列 preview 分类。

### 执行顺序（Agent 必读）

1. **加载旧库**：`wiki/index.md`、`taxonomy.json`、`faqs/`、`concepts/`、`categories/`
2. **逐篇读 raw 原文**：`raw/websites/**/*.md` 或 `raw/chats/session_*.md`（含链接与附件块）
3. **分类与概念**：动态 `categories` / `concepts`，禁止写死在 Python
4. **摘要**：每篇/每会话 1～3 句 `summary`
5. **关联**：`related_wikilinks`、`related_doc_urls`、`related_concept_ids`
6. **增量**：同 `source_url` / 相似问法 → 更新；新内容 → 新增；未涉及旧页 → 保留

### 网站流程

1. `ingest_websites.py --export-taxonomy-queue` → 清单（索引，非正文）
2. Agent 通读每篇 raw → 写 `wiki/taxonomy.json`
3. `ingest_websites.py --mode agent --taxonomy wiki/taxonomy.json`

### 会话流程

1. `ingest_wiki.py --export-queue` → `manifest.json`
2. Agent 通读每条 `source_file` → `extractions/<session_id>.json`
3. `ingest_wiki.py --mode agent --extractions-dir ...`

### Query（index-first，与 ingest 同属 Agent 职责）

1. 读 `wiki/index.md` → 选定 1～2 个 `[[分类-...]]`
2. 并行读 `wiki/categories/分类-*.md` → 从主题内总结定位 `[[问答-...]]`
3. 并行读 `wiki/faqs/问答-*.md` → 提取「标准回答」「操作步骤」
4. 并行全库关键词检索 `wiki/categories/` + `wiki/faqs/` + `wiki/concepts/`（召回补充证据）
5. 整合 2/3/4 的结果，去重并处理冲突
6. Agent 生成最终回复并携带 `[[来源页]]`；`raw/chats/` 仅作校验，不替代 FAQ

禁止跳过 index 直接执行并行检索。

### taxonomy.json 示例

```json
{
  "source": "cursor-agent",
  "categories": [{"id": "hpc-job-slurm", "title": "高性能计算与作业调度", "overview": "..."}],
  "concepts": [{"id": "slurm", "title": "Slurm", "definition": "...", "related_doc_indices": [3, 8]}],
  "assignments": [{"source_url": "https://www.scnet.cn/help/docs/...", "category_id": "hpc-job-slurm"}],
  "doc_enrichments": [
    {
      "source_url": "https://www.scnet.cn/help/docs/...",
      "summary": "介绍 Slurm 作业提交与队列选择。",
      "related_concept_ids": ["slurm"],
      "related_doc_urls": ["https://www.scnet.cn/help/docs/other-page"]
    }
  ]
}
```

### 会话 extraction JSON 示例

```json
{
  "skip": false,
  "summary": "访客咨询高斯16资源配置，客服给出控制台修改步骤。",
  "category_id": "resource-queue",
  "question": "高斯16如何更换资源配置？",
  "answer": "客服说明：...",
  "steps": ["打开控制台..."],
  "tags": ["高斯"],
  "related_wikilinks": ["概念-Gaussian高斯", "分类-计算资源与队列"]
}
```

---

## 会话清洗与萃取

### 发送方类型（与 `process_chat_excel.py` 一致）

| 值 | 含义 | 处理 |
|----|------|------|
| 1 | 客服 | **高价值内容优先来源**；过滤要求至少一条实质客服回复 |
| 2 | 访客 | 上下文/诉求来源 |
| 3 | 系统 | 通常丢弃 |
| 4 | 机器人 | 保留有信息量的；丢弃纯模板；**不计入**「有客服回复」 |

### 会话过滤（`is_session_meaningful`）

- `has_meaningful_agent`：至少一条客服消息通过噪声/寒暄过滤且具实质内容
- `substantive_score >= 1`：访客或客服存在链接、足够汉字或较长文本
- **仅访客**（客服未回复）→ 不生成 raw 文件
- 实现：`scripts/kb_utils.py`

### 核心原则

- **一会话一萃取**：以 `会话ID` 为单位，Agent **先通读** raw 再写 `extractions/*.json`。
- **合并进 FAQ**：由 `ingest_wiki.py` 写入 `wiki/faqs/`，相似问法合并，禁止每会话单独 wiki 页。
- **跨会话归纳**：重复概念写入 `wiki/concepts/`，用 `related_wikilinks` 链回 FAQ。

### 会话 markdown 模板（raw/chats，脚本输出）

```markdown
---
source_type: chat
session_id: "abc123"
message_count: 12
time_range: "2024-04-02 23:58:53 ~ 2024-04-03 00:15:22"
file_urls:
  - https://example.com/manual.pdf
---

# 会话 abc123

## 对话记录
- [2024-04-02 23:59:01] **访客**: 订单一直显示处理中，想退款
- [2024-04-03 00:01:12] **客服**: https://docs.example.com/refund-guide

## 链接与附件内容

### [网页] refund-guide

来源: https://docs.example.com/refund-guide

（网页正文，由 enrich_sessions 内联写入，不存 attachments 目录）
```

### 链接/OCR 脚本约束

| 允许（Excel 清洗**必须**带 enrich 参数） | 禁止 |
|------|------|
| `enrich_sessions.py --chats-dir ... --with-ocr` | `raw/attachments/` 目录 |
| `process_chat_excel.py ... --enrich-links --with-ocr` | 省略 enrich 参数后仅清洗 |
| `ingest_wiki.py` / `ingest_websites.py` 默认增量 | 未加 `--full-rebuild` 时清空 wiki |
| | `ocr_images.py` / `scrape_urls.py` CLI |
| | `_file_urls.txt` |

### 会话 wiki 页模板（wiki/sessions，Agent ingest 输出）

```markdown
---
type: session
session_id: "abc123"
tags: [退款, 订单]
sources:
  - "[[source-session-abc123]]"
updated_at: 2026-05-19
value: high
---

# 会话 abc123 — 订单处理中申请退款

## 会话摘要
访客订单长期处于处理中状态，咨询退款方式；客服提供完整操作手册链接并说明按步骤提交申请。

## 关键信息
- 场景：订单状态「处理中」
- 客服指引：通过操作手册链接按步骤提交退款
- 附件：[[source-attachment-manual-xxx]] 含逐步截图说明

## 操作与政策摘录
- 退款入口：App → 我的订单 → 选择订单 → 申请退款
- （若会话未明确时效，不写猜测性政策）

## 对话背景（可选，非 QA）
访客已等待多日，语气较急；客服未承诺具体到账时间，仅提供文档。

## 相关
- [[procedure-refund-apply]]
```

### 价值判断（Agent）

| 信号 | 处理 |
|------|------|
| 人工客服完整步骤/政策说明 | 写入「关键信息」「操作与政策摘录」 |
| 仅寒暄、超时关闭 | `value: low`，跳过或极简摘要 |
| 含 fileUrl | 抓取后合并附件要点进同页 |
| 机器人纯模板 | 不写入 wiki |

### 跨会话归纳（可选）

- 3+ 会话出现相同流程描述 → 更新 `wiki/procedures/`，`sources` 列出相关 `[[session-xxx]]`
- 术语/产品定义反复出现 → 更新 `wiki/concepts/`
- 表述冲突 → `contradiction` flag，**不**强行合并为统一「标准回答」

---

## wiki 页面模板

### Session（Excel 来源唯一产出）

见上文「会话 wiki 页模板」。**禁止**以下结构：

```markdown
## 标准回答
Q: ...
A: ...
```

### Concept / Procedure（网站或跨会话归纳）

```markdown
---
type: procedure
tags: [退款]
sources:
  - "[[source-website-refund-page]]"
  - "[[session-abc123]]"
---

# 申请退款流程

1. 打开 App → 我的订单
2. ...
```

### Source 摘要页

```markdown
---
type: source
source_type: chat
source_id: session_abc123
ingested_at: 2026-05-19
---

# 来源：会话 abc123

## 摘要
订单处理中退款咨询；已萃取至 [[session-abc123]]。

## 萃取清单
- [[session-abc123]] — 新建（会话级要点，非 QA）

## 原始路径
`raw/chats/session_abc123.md`
```

---

## index.md 表格格式

```markdown
| 页面 | 类型 | 来源数 | 更新 |
|------|------|--------|------|
| [[session-abc123]] | session | 1 | 2026-05-19 |
| [[procedure-refund-apply]] | procedure | 4 | 2026-05-19 |
```

---

## 与 llm-wiki-agent 的差异

| llm-wiki-agent | 本知识库 |
|----------------|----------|
| entities/ | concepts/ + **sessions/** |
| FAQ 式问答 | **会话级要点萃取，不做 QA** |
| 通用 ingest | 网站爬虫 + Excel 会话管线 |

哲学一致：**编译一次、持续累积、ingest 时发现矛盾**。
