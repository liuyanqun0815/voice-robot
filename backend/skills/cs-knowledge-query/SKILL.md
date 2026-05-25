---
name: cs-knowledge-query
description: Answers customer-service questions via query_kefu_wiki tool (trial, billing, API, jobs, platform). Use for any policy/how-to Q&A. Do NOT read wiki files manually.
---

# 客服知识库查询

**唯一动作：** 对用户问题调用工具 **`query_kefu_wiki(question)`**（参数为完整原问）。

## 禁止

- 不要 `read_file` / `grep` / `ls` 扫 `/kefu-know/wiki/`
- 不要先读本 SKILL 再重复检索；工具已含 index、主题页、FAQ 标准答

## 作答

- 仅根据工具返回的「标准回答」与 `来源：wiki/...` 用简洁中文答复（先结论，后短步骤）
- 无依据则说明知识库未收录，勿编造

## 其它

- 建库 / ingest：`cs-knowledge-wiki`
- 实现：`app/services/wiki_query.py`
