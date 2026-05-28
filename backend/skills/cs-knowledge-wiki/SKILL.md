---
name: cs-knowledge-wiki
description: Builds and maintains customer-service wiki from website crawls and Excel chats (ingest, taxonomy, FAQ merge, lint, graph). Agent reads raw files, classifies, summarizes, cross-links, incrementally updates wiki. Use for ingest website/chat, export queue, lint, build graph—not for end-user Q&A (use cs-knowledge-query).
---

# 智能客服知识库 Wiki

参考 [llm-wiki-agent](https://github.com/SamurAIGPT/llm-wiki-agent)：**一次萃取、持久沉淀、交叉引用、增量更新**——非每次查询临时 RAG。

## 目录结构

```
raw/
├── websites/          # 网站抓取原始 markdown
└── chats/             # Excel 会话（含对话记录 + 链接/附件正文，一体保存）
wiki/
├── index.md           # 可读知识索引（按主题分类，仅 FAQ/流程）
├── log.md             # 追加式操作日志
├── overview.md        # 跨主题总览
├── categories/        # 主题页：**内含该主题 热点FAQ 总结 + 操作流程汇总**
├── faqs/              # 合并后的客户问答（相似问题合一文件）
├── concepts/          # 产品概念（**从 FAQ 归纳**，非独立编造）
└── syntheses/         # query 答案归档
# 无 wiki/sessions/、无 wiki/procedures/；会话仅在 raw/chats/
graph/
├── graph.json
└── graph.html
```

## 触发词

| 用户说法 | 动作 |
|---------|------|
| `ingest website <url>` | 抓取站点 → raw → wiki |
| `ingest chat <excel>` | 清洗会话（**必带** `--enrich-links --with-ocr`）→ raw → wiki |
| `ingest <path>` | 通用 ingest（md/已转换内容） |
| `lint` | 孤儿页、断链、矛盾、缺口 |
| `build graph` | 重建 graph.html |

## 工作流总览

```
Task Progress:
- [ ] 1. 采集（网站 / Excel / fileUrl）→ 写入 raw/
- [ ] 2. 【必做】链接内联：网页抓正文 + 图片 OCR → `## 链接与附件内容`
- [ ] 3. 脚本导出 Agent 队列（--export-queue / --export-taxonomy-queue）
- [ ] 4. 【Agent 核心】逐篇阅读 raw → 分类/概念 → 摘要 → 关联引用 → 写 JSON
- [ ] 5. 脚本增量合并（--mode agent）→ 更新 index / overview / log
- [ ] 6. lint → 修复 → build graph（可选）
```

**Agent 执行 Excel 清洗时，必须带 `--enrich-links --with-ocr`，禁止省略。**

---

## Agent 萃取职责（必读）

**大模型由上层 Cursor Agent 执行；`ingest_*.py` 只导出队列与合并入库，禁止在脚本内调 API。**

### 原则：先读原文，再归纳；增量更新，不重复造库

| 顺序 | Agent 必须做的事 | 禁止 |
|------|------------------|------|
| 0 | **加载已有 wiki**：`index.md`、`categories/`、`faqs/`、`concepts/`、`taxonomy.json`（若存在） | 未读旧库就全量重写 |
| 1 | **逐篇阅读 raw 原文**（`raw/chats/session_*.md` 或 `raw/websites/**/*.md`），含 `## 对话记录` 与 `## 链接与附件内容` | 仅凭 manifest 预览分类 |
| 2 | **动态分类与概念**：归纳主题 `categories`、术语 `concepts`，为每篇分配 `category_id` | 在 Python 中写死分类表 |
| 3 | **生成摘要**：会话写 `summary`；网站写 `doc_enrichments[].summary`；主题页 `overview` 可修订 | 跳过摘要、只堆正文 |
| 4 | **建立关联引用**：`related_wikilinks`、`related_concept_ids`、`related_doc_urls` / `sources` | 孤立页、无 `[[wikilink]]` |
| 5 | **增量合并**：同 `source_url` / 相似问法 → **更新**；新知识 → **新增**；未涉及旧页 → **保留** | 默认清空 `wiki/` |

### 网站文档（Agent 步骤）

1. 运行 `ingest_websites.py --export-taxonomy-queue` 得到 `taxonomy-request.json`（清单仅作索引，**不能代替阅读原文**）。
2. 对清单中每一篇（或本批新增/变更篇）：
   - 打开对应 `raw/websites/**/<文件名>.md`，**通读全文**；
   - 写 1～3 句 `summary`；归入 `category_id`；挂到相关 `concept`；
   - 在 `doc_enrichments` 中注明 `related_doc_urls` / `related_concept_ids`。
3. 汇总写入 `wiki/taxonomy.json`（含 `categories`、`concepts`、`assignments`、`doc_enrichments`）。
4. 运行 `ingest_websites.py --mode agent --taxonomy wiki/taxonomy.json` 合并；脚本按 `source_url` 更新已有 `faqs/*.md`，新增未出现的文档。

### 客服会话（Agent 步骤）

1. 运行 `ingest_wiki.py --export-queue` → `manifest.json` + `PROMPT.md`。
2. 对 `pending` 中每条：
   - **完整阅读** `source_file` 会话 markdown；
   - 输出 `extractions/<session_id>.json`（含 `summary`、`question`、`answer`、`steps`、`related_wikilinks`）。
3. 运行 `ingest_wiki.py --mode agent --extractions-dir ...`；脚本将相似问法**合并**进已有 FAQ，更新 `categories/` 内主题总结，刷新 `concepts/` 关联。

### 增量更新判定

| 匹配键 | 行为 |
|--------|------|
| 网站 `source_url` 与已有 `wiki/faqs/*.md` frontmatter 一致 | 更新该页摘要/正文/分类，**保留原中文文件名** |
| FAQ 问法与已有条目相似（脚本阈值约 0.65） | 合并问法变体，刷新标准回答与步骤 |
| 新概念 `concept_id` 已存在 | 合并定义与 `related_faq_names`，不删旧关联 |
| 本批未出现的旧 FAQ / 旧文档 | **保留** |
| 用户明确要求从零重建 | 仅此时使用 `--full-rebuild` |

---

## 数据源 1：网站全站抓取

**执行脚本**（优先于手写爬虫）：

```bash
python scripts/scrape_website.py \
  --base-url "https://www.scnet.cn/ac/openapi/doc/2.0/moduleapi/tutorial/apicall.html" \
  --output kefu-know/raw/websites/scnet-api/ \
  --max-depth 5 \
  --delay 0.5 \
  --clean

# 网站萃取见下文「Ingest 工作流」（Agent 生成 taxonomy，脚本不调 API）
```

规则见 [reference.md](reference.md#网站抓取)。抓取完成后：

1. 每个页面保存为 `raw/websites/<核心主题>.md`（中文/语义化文件名，非 URL 哈希）
2. frontmatter 含 `source_url`、`fetched_at`、`title`、`filename_core`
3. 正文 UTF-8 正确解码，无乱码
4. **分类/概念由上层 Agent 写入 `wiki/taxonomy.json`**，脚本只负责合并

---

## 数据源 2：Excel 客服聊天记录

### Excel 列映射

| 列 | 字段 | 用途 |
|----|------|------|
| A | 会话ID | 分组键 |
| B | 消息唯一标识 | 去重 |
| C | 消息发送方类型 | **1客服** 2访客 3系统 4机器人 |
| D–E | 发送方ID/名称 | 上下文 |
| F | 消息类型 | 过滤非文本 |
| G | 消息内容 | 主文本 |
| H | createTime | 排序 |
| I–J | fileKey/fileName | 附件元数据 |
| K | **fileUrl** | **必须抓取并 ingest** |
| L–M | sendStatus/sensitiveWord | 过滤敏感/失败 |

### 执行脚本（**必须**带链接内联参数）

```bash
# 全量替换 raw/chats（默认清空旧 session_*.md）
python scripts/process_chat_excel.py \
  --input path/to/chats.xlsx \
  --output kefu-know/raw/chats/ \
  --enrich-links --with-ocr

# 增量追加（不删历史，覆盖同名会话）
python scripts/process_chat_excel.py \
  --input path/to/chats.xlsx \
  --output kefu-know/raw/chats/ \
  --append \
  --enrich-links --with-ocr
```

**强制要求（Agent 不得省略）：**

| 参数 | 作用 |
|------|------|
| `--enrich-links` | 清洗完成后自动调用 `enrich_sessions.py`，抓取会话内 URL |
| `--with-ocr` | 对图片链接做 OCR，有意义才写入 |

脚本流程：按会话ID分组 → 清洗 → 输出 `session_<id>.md` → **抓取链接/附件正文**写入 `## 链接与附件内容`（**不**使用 `raw/attachments/`）。

未带上述参数视为流程不完整；`ingest_wiki.py` 无法整合链接正文进 FAQ/主题页。

### 会话萃取原则（重要）

**按「本次会话」整体萃取有价值内容，禁止拆成 QA 问答对。**

脚本输出按时间线的 `## 对话记录`；Agent **必须先通读**整段会话（含 `## 链接与附件内容`），再写入 `extractions/<session_id>.json`（`summary` + 标准问法 + 完整回答 + 步骤 + 关联），由 `ingest_wiki.py` 合并进 `wiki/faqs/` 与 `wiki/categories/`。**不要**生成 `wiki/sessions/` 目录。

**丢弃（清洗阶段，无意义会话直接不写文件）：**
- **仅访客发言**（无客服实质回复）→ 不写 `session_*.md`
- 系统提示：`访客离开超时`、`会话已关闭`、纯寒暄、`nan`
- 仅「访客接入座席」「关闭会话」等系统流转，无访客/客服实质内容
- 重复机器人兜底话术、`sensitiveWord` 非空
- 单字/表情；**不满足 `is_session_meaningful` 的整段会话忽略**（须含客服实质回复 + 实质内容分 ≥ 1）

**OCR/附件：** 识别结果无实质文字（`is_meaningful_text` 不通过）→ 不写入、不合并进会话

**URL 来源：** 从各 `session_*.md` 的 frontmatter `file_urls` 读取，**不生成** `_file_urls.txt`

**有价值信号（写入 sessions 页）：**
- 客服(类型1) 给出的完整说明、操作指引、政策口径
- 访客描述的问题背景、场景、约束（作为上下文，不单独成「问」）
- 会话中达成的结论、待办、补偿方案
- 对话中的链接/图片：抓取后写在同一会话 `## 链接与附件内容`（不单独存附件目录）

**跨会话归纳（Agent 负责）：**
- 多会话反复出现的**同一概念** → 更新 `wiki/concepts/`，`related_wikilinks` 指向相关 `[[问答-...]]`
- 相似问法由 ingest **合并**为单条 FAQ，勿为每会话单独建页

**低价值会话：** 仅寒暄/无实质内容 → 跳过 ingest，在 `log.md` 记 `skipped`

完整规则：[reference.md](reference.md#会话清洗与萃取)

---

## 链接与附件（内联在会话中，**必做步骤**）

**禁止 `raw/attachments/`。** 勿运行 `ocr_images.py` / `scrape_urls.py` CLI（旧版 `--attachments-dir` 会误写该目录）。

| 类型 | 处理 |
|------|------|
| 网页 / docx / pdf / txt | 提取正文，内联至 `## 链接与附件内容` |
| 图片 png/jpg 等 | `--with-ocr` OCR，有意义才内联 |
| 无意义 OCR/正文 | 不写入 |

**标准路径（Excel 清洗，Agent 必须执行）：** 见上文 `process_chat_excel.py ... --enrich-links --with-ocr`。

**补救路径（仅当历史 raw 漏跑 enrich 时）：**

```bash
python scripts/enrich_sessions.py \
  --chats-dir kefu-know/raw/chats/ \
  --with-ocr
# 重抓全部：加 --force
```

验收：每个含 URL 的 `session_*.md` 在 `## 对话记录` 之后应有 `## 链接与附件内容`（抓取失败则无该块，须在 `log.md` 记录）。

- 对话中的 **http 链接**（如客服发的教程 URL）写入 frontmatter `file_urls` 并抓取正文到会话下方
- **纯寒暄会话** → 不生成 session 文件

---

## Ingest 工作流（核心）

详见上文 **[Agent 萃取职责（必读）](#agent-萃取职责必读)**。脚本只做：导出队列 → Agent 读原文并写 JSON → 脚本增量合并。

### 客服会话 → wiki

```bash
# 1) 规则萃取（无需 Agent）
python scripts/ingest_wiki.py \
  --chats-dir kefu-know/raw/chats/ \
  --wiki-dir kefu-know/wiki/ \
  --mode heuristic

# 2) 导出待 Agent 萃取的会话队列
python scripts/ingest_wiki.py \
  --chats-dir kefu-know/raw/chats/ \
  --wiki-dir kefu-know/wiki/ \
  --export-queue kefu-know/wiki/.agent-queue/

# 3) Agent 全量萃取（通读 raw → JSON + 可选 FAQ 草稿 markdown）
python scripts/agent_extract_all_sessions.py \
  --write-drafts \
  --wiki-dir kefu-know/wiki/

# 4) 合并 Agent 萃取 → wiki/faqs/*.md（含 [[wikilink]] 引用）
python scripts/ingest_wiki.py \
  --chats-dir kefu-know/raw/chats/ \
  --wiki-dir kefu-know/wiki/ \
  --mode agent \
  --extractions-dir kefu-know/wiki/.agent-queue/extractions/
```

### 网站文档 → wiki

```bash
# 1) 导出 taxonomy 请求（文档清单）
python scripts/ingest_websites.py \
  --websites-root kefu-know/raw/websites/ \
  --wiki-dir kefu-know/wiki/ \
  --export-taxonomy-queue kefu-know/wiki/.agent-queue/taxonomy/

# 2) Agent：逐篇阅读 raw/websites 原文 → 生成 kefu-know/wiki/taxonomy.json
#    （categories、concepts、assignments、doc_enrichments 摘要与关联）

# 3) 合并入库
python scripts/ingest_websites.py \
  --websites-root kefu-know/raw/websites/ \
  --wiki-dir kefu-know/wiki/ \
  --mode agent \
  --taxonomy kefu-know/wiki/taxonomy.json
```

### Wiki 增量策略（**默认，Agent 必须遵守**）

| 情况 | 行为 |
|------|------|
| 与已有条目相同（FAQ 问法相似 / 网站同 `source_url`） | **更新**对应 `wiki/faqs/*.md`（保留原文件名） |
| 不存在的新知识 | **新增**文件 |
| 本次未涉及的历史条目 | **保留**，禁止删除 |
| 需清空重建 | 显式加 `--full-rebuild`（慎用，会 `rmtree` faqs/concepts/categories） |

**禁止**在未加 `--full-rebuild` 时清空 `wiki/faqs/`、`wiki/concepts/`、`wiki/categories/`。

**产出规则：**

| 产物 | 规则 |
|------|------|
| `wiki/index.md` | 可读主题索引（丰富分类名 + 概述 + 高频问题） |
| `wiki/categories/` | 每主题**一篇**，内嵌该主题全部 FAQ 总结与操作流程 |
| `wiki/faqs/` | 相似问答**合并**为单文件（问法变体 + 标准回答 + 步骤） |
| `wiki/concepts/` | 从 FAQ 文本**归纳**产品/术语概念，链回 FAQ |
| 禁止 | `wiki/sessions/`、`wiki/procedures/` 独立目录 |

**Ingest 前置条件：** 所有 `session_*.md` 须已完成链接内联（`--enrich-links --with-ocr` 或 `enrich_sessions.py`）。未完成则先补跑 enrich，再 ingest。

**Ingest 步骤（chat → wiki，Agent + 脚本分工）：**

| 步骤 | 执行者 | 动作 |
|------|--------|------|
| 1 | 脚本 | 加载已有 `wiki/faqs/`、`concepts/` |
| 2 | **Agent** | 通读每条 `raw/chats/session_*.md`，写 `extractions/*.json` |
| 3 | **Agent** | `summary` + 整合链接附件要点进 `answer` / `steps` |
| 4 | 脚本 | 按问法相似度合并 FAQ（≈0.65）：**更新**已有 / **新增**条目 |
| 5 | 脚本 | 刷新 `categories/` 主题内总结、`concepts/` 关联（`[[wikilink]]`） |
| 6 | 脚本 | 更新 `index.md`、`overview.md`、`log.md`（记 skipped/updated/added） |

### 命名约定

- 全部使用**中文文件名**，体现核心内容：
  - 主题：`分类-计算资源与队列-中心开通-CPU架构-节点配额.md`
  - 问答：`问答-高斯16如何更换资源配置.md`
  - 概念：`概念-Gaussian高斯量子化学软件.md`
- Wikilink 与文件名一致（不含 `.md`）

---

## 更新策略（非重复造库）

| 场景 | 行为 |
|------|------|
| 新一批 chat / 网站 raw | `ingest_wiki.py` / `ingest_websites.py` **默认增量**（更新+新增，不删） |
| 同 FAQ 问法 / 同网站 `source_url` | 覆盖更新对应 wiki 页，保留文件名 |
| 同会话ID 新 Excel | 清洗后 `ingest_wiki.py`，合并入已有 FAQ |
| 同主题多会话 | 合并入同主题 FAQ，更新 category 内总结 |
| 需从零重建 wiki | 仅当用户明确要求时使用 `--full-rebuild` |
| 附件 URL 失效 | log 标记 `fetch_failed`，保留上次成功内容 |

---

## 知识库查询（Query）

客服问答、语音查库、自然语言提问（如试用期限、退款到账）**不在本技能内执行**，请使用独立技能：

- **`cs-knowledge-query`**：`/skills/cs-knowledge-query/SKILL.md`
- 虚拟路径：`/kefu-know/wiki/`（index → categories → faqs → grep）

---

## Lint

维护脚本（`scripts/`）：`kb_check.py` 汇总断链与孤儿；`kb_autofix_links.py` 先模糊修正 wikilink，再对 **categories / concepts** 删除指向不存在 `.md` 的死链，并对标题含 `[表情]` 等无法在 `[[...]]` 中闭合的条目改写为 `[标题](faqs/… .md)` 等相对链接。

检查项：
- [ ] `index.md` 与磁盘文件一致
- [ ] 无孤儿页（未被 index 或 wikilink 引用）
- [ ] 断链 `[[...]]`
- [ ] 主题页是否包含「主题内问答总结」
- [ ] 相似内容是否已合并（避免重复文件）
- [ ] concepts 是否链回 faq

相似合并 / 去重执行约束（强制）：
- 必须由 Agent 按层级索引执行：先读 `wiki/index.md`，再读 `wiki/categories/`，最后读 `wiki/concepts/`。
- 合并与去重前，先基于上层索引定位候选，再在当前层内做相似度分组；禁止跳过 `index.md` 直接全量盲扫。
- 去重对象包含：重复 FAQ 链接、语义近似标题链接、同一目标的别名链接（如带 hash 后缀与无后缀并存）。
- 合并后需回写上层索引引用关系：`index.md`、分类页、概念页的链接必须同步到最终保留页，避免新增孤儿或断链。

---

## 依赖

```bash
pip install pandas openpyxl requests beautifulsoup4 trafilatura lxml
```

## 附加资源

- Excel 字段、清洗细则、页面模板：[reference.md](reference.md)
- 可执行脚本：`scripts/`（优先执行，勿重复造轮子）

## 脚本分层（维护约定）

- 核心脚本（长期保留）：`process_chat_excel.py`、`enrich_sessions.py`、`ingest_wiki.py`、`ingest_websites.py`、`agent_extract_all_sessions.py`、`kb_check.py`、`kb_autofix_links.py`、`kb_fix_orphans.py`、`kb_audit_lint.py`、`kb_audit_autofix.py`。
- 核心库模块（长期保留）：`agent_ingest.py`、`kb_utils.py`、`ingest_chats.py`、`wiki_taxonomy_llm.py`、`scrape_urls.py`、`ocr_images.py`（后两者作为库，不直接 CLI 运行）。
- 辅助脚本（可归档）：`build_draft_review_index.py`、`fix_extraction_names.py`、`agent_batch_extract_sessions.py`。默认放置在 `scripts/_archive/`，仅在排障或历史兼容时使用。
- 删除策略：先归档到 `scripts/_archive/` 并完成一轮全流程验证（ingest/lint/autofix），确认无回归后再考虑物理删除。
