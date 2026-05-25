# 使用示例

## 0. 会被丢弃的会话示例

仅访客提问、客服未回复 → **不生成** `session_*.md`：

```markdown
- [时间] **访客**: 请问流体仿真软件市场价是多少？
```

须有客服实质回复才会保留。

## 1. Excel 导入（会话 + 内联链接内容，**必带 enrich 参数**）

```bash
# 全量
python .cursor/skills/cs-knowledge-wiki/scripts/process_chat_excel.py \
  --input data/customer_chats.xlsx \
  --output kefu-know/raw/chats/ \
  --enrich-links --with-ocr

# 增量（不删历史）
python .cursor/skills/cs-knowledge-wiki/scripts/process_chat_excel.py \
  --input data/chat_20260404.xlsx \
  --output kefu-know/raw/chats/ \
  --append \
  --enrich-links --with-ocr
```

单会话文件结构：

```markdown
## 对话记录
- [时间] **访客**: 为什么收不到飞书信息
- [时间] **客服**: https://www.scnet.cn/help/docs/.../feishu/

## 链接与附件内容

### [网页] feishu

来源: https://www.scnet.cn/help/docs/.../feishu/

飞书机器人接入
第一步：创建飞书应用
...
```

## 2. 抓取帮助中心

```bash
python .cursor/skills/cs-knowledge-wiki/scripts/scrape_website.py \
  --base-url "https://help.example.com" \
  --output kefu-know/raw/websites/
```

## 3. Agent

```
@cs-knowledge-wiki ingest kefu-know/raw/chats/
```
