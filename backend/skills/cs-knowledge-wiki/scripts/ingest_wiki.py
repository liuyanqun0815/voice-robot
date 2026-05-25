#!/usr/bin/env python3
"""从 raw/chats 构建分层知识库：主题页（含 FAQ 内总结+流程）、合并 FAQ、概念归纳。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

from ingest_chats import parse_session_file, substantive_messages
from kb_utils import extract_urls_from_text, is_meaningful_text, is_noise

STEP_HINTS = re.compile(
    r"(第一步|第二步|第三步|第\d+步|点击|打开|进入|选择|在.+?里|按照|步骤|教程)",
    re.I,
)
LINK_BODY_MAX_FAQ = 2500
LINK_BODY_MAX_SUMMARY = 900

# (id, 丰富标题, 主题概述, 匹配关键词)
CATEGORIES: dict[str, tuple[str, str, list[str]]] = {
    "software-license": (
        "科学软件与许可证（Gaussian / VASP / 环境安装）",
        "涵盖科学计算软件的购买、许可证、安装部署、版本选择与区域开通。",
        [r"高斯", r"gaussian", r"g16", r"vasp", r"许可", r"license", r"安装", r"conda", r"软件", r"激活", r"部署"],
    ),
    "resource-queue": (
        "计算资源与队列（中心开通 / CPU 架构 / 节点配额）",
        "涵盖计算中心选择、队列资源、CPU 架构匹配、节点创建与资源不足排查。",
        [r"资源", r"中心", r"队列", r"cpu", r"核时", r"节点", r"实例", r"开通", r"架构", r"海光", r"amd", r"intel"],
    ),
    "job-schedule": (
        "作业提交与调度（Slurm / 排队 / 任务异常）",
        "涵盖作业脚本、提交排队、运行失败、无输出、空转与调度系统使用。",
        [r"作业", r"提交", r"排队", r"调度", r"任务", r"slurm", r"pbs", r"报错", r"作业号"],
    ),
    "billing-account": (
        "账户充值与计费（订单 / 发票 / Token 购买）",
        "涵盖账户余额、充值缴费、订单发票、试用扣费与优惠活动。",
        [r"账户", r"充值", r"计费", r"发票", r"余额", r"扣费", r"订单", r"退款", r"试用"],
    ),
    "network-access": (
        "网络连接与登录（SSH / 远程桌面 / 访问异常）",
        "涵盖登录失败、SSH/VSCode 远程连接、网络访问与账号状态。",
        [r"ssh", r"vpn", r"登录", r"连接", r"访问", r"密码", r"远程", r"桌面", r"连不上"],
    ),
    "data-transfer": (
        "数据传输与存储（上传 / 下载 / 跨区迁移）",
        "涵盖文件上传下载、存储空间、跨计算区传参与数据迁移。",
        [r"上传", r"下载", r"传输", r"存储", r"文件", r"数据", r"迁移", r"空间不足"],
    ),
    "openclaw-feishu": (
        "OpenClaw 与飞书集成（龙虾部署 / 机器人 / 消息收发）",
        "涵盖 OpenClaw 一键部署、飞书机器人配置、消息不通与云端休眠。",
        [r"飞书", r"feishu", r"openclaw", r"龙虾", r"机器人", r"sclaw", r"open claw"],
    ),
    "api-token": (
        "开放平台 API 与大模型 Token（Key / 调用 / 余额）",
        "涵盖 API Key 申请、模型调用、Token 余额、兑换码与第三方工具对接。",
        [r"api", r"token", r"openapi", r"模型", r"llm", r"key", r"余额", r"兑换"],
    ),
    "general": (
        "平台通用与其他咨询（镜像 / 认证 / 合作）",
        "未归入上述主题的通用咨询、平台能力与合作入驻等。",
        [],
    ),
}

CONCEPT_DEFS: dict[str, tuple[str, str, list[str]]] = {
    "gaussian": ("Gaussian 高斯量子化学软件", "用于量子化学与分子模拟的计算软件，需在匹配 CPU 架构的队列上开通。", [r"高斯", r"gaussian", r"g16"]),
    "openclaw": ("OpenClaw（龙虾）智能助手", "超算互联网上的智能助手服务，支持飞书等渠道接入。", [r"openclaw", r"龙虾", r"sclaw"]),
    "slurm": ("Slurm 作业调度", "集群作业调度系统，用于提交与管理计算任务。", [r"slurm", r"作业调度", r"sbatch"]),
    "token": ("大模型 Token", "调用平台大模型 API 的计量单位，可在用量页查看与购买。", [r"token", r"百万tokens", r"用量"]),
    "compute-center": ("计算中心与区域", "不同地理与硬件架构的计算资源区，软件开通需选择匹配中心。", [r"计算中心", r"华东", r"华北", r"雄衡", r"乌镇", r"昆山"]),
}

SIMILARITY_THRESHOLD = 0.68


INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n]')


@dataclass
class LinkBlock:
    """会话 ## 链接与附件内容 中的单条解析结果。"""

    url: str
    title: str
    body: str
    kind: str = "网页"  # 网页 | 图片 OCR


@dataclass
class FaqItem:
    question: str
    answer: str
    category_id: str
    variants: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    link_blocks: list[LinkBlock] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    related_wikilinks: list[str] = field(default_factory=list)
    source_count: int = 1
    file_name: str = ""  # 中文文件名（无扩展名），如 问答-高斯16如何更换资源配置


@dataclass
class ConceptItem:
    concept_id: str
    title: str
    definition: str
    file_name: str = ""
    points: list[str] = field(default_factory=list)
    related_faq_names: list[str] = field(default_factory=list)
    category_ids: list[str] = field(default_factory=list)


def category_title(cat_id: str) -> str:
    return CATEGORIES[cat_id][0]


def category_summary(cat_id: str) -> str:
    return CATEGORIES[cat_id][1]


def infer_category_id(text: str) -> str:
    lower = text.lower()
    best_id, best_score = "general", 0
    for cat_id, (_, _, patterns) in CATEGORIES.items():
        if cat_id == "general":
            continue
        score = sum(1 for p in patterns if re.search(p, lower, re.I))
        if score > best_score:
            best_score, best_id = score, cat_id
    return best_id


def normalize_question(q: str) -> str:
    q = re.sub(r"\s+", "", q)
    q = re.sub(r"[？?！!。．,，、；;：:\"'“”【】\[\]()（）]", "", q)
    return q.lower()


def question_similarity(a: str, b: str) -> float:
    na, nb = normalize_question(a), normalize_question(b)
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        return 0.85
    return SequenceMatcher(None, na, nb).ratio()


def sanitize_filename_text(text: str) -> str:
    text = INVALID_FILENAME_CHARS.sub("", text)
    text = re.sub(r"\s+", "", text)
    return text.strip(". ")


def refine_faq_core_title(question: str, max_len: int = 42) -> str:
    q = question.strip().rstrip("？?！!。.")
    q = re.sub(r"^(你好|您好|请问|麻烦|想问|老师|应用支持工程师|人工客服)[，,、\s]*", "", q, flags=re.I)
    q = re.sub(r"^[@＠][^\s]+[，,、\s]*", "", q)
    q = sanitize_filename_text(q)
    if len(q) > max_len:
        q = q[:max_len].rstrip("，、的了吗呢")
    return q or "未命名问题"


def assign_unique_name(base: str, seed: str, used: set[str]) -> str:
    name = sanitize_filename_text(base)
    if not name:
        name = "未命名"
    if name not in used:
        used.add(name)
        return name
    suffix = hashlib.md5(seed.encode()).hexdigest()[:6]
    candidate = f"{name}-{suffix}"
    n = 2
    while candidate in used:
        candidate = f"{name}-{suffix}{n}"
        n += 1
    used.add(candidate)
    return candidate


def faq_file_name(question: str, used: set[str]) -> str:
    core = refine_faq_core_title(question)
    return assign_unique_name(f"问答-{core}", question, used)


def category_file_name(cat_id: str, used: set[str]) -> str:
    title = CATEGORIES[cat_id][0]
    short = re.sub(r"[（(]([^）)]+)[）)]", r"-\1", title)
    short = re.sub(r"[/\s]+", "-", short)
    short = sanitize_filename_text(short)
    return assign_unique_name(f"分类-{short}", cat_id, used)


def concept_file_name(title: str, concept_id: str, used: set[str]) -> str:
    short = sanitize_filename_text(title)
    return assign_unique_name(f"概念-{short}", concept_id, used)


def assign_all_file_names(
    merged_faqs: list[FaqItem],
    concepts: dict[str, ConceptItem],
    faq_by_cat: dict[str, list[FaqItem]],
    reserved_names: set[str] | None = None,
    existing_cat_names: dict[str, str] | None = None,
) -> dict[str, str]:
    used: set[str] = set(reserved_names or [])
    cat_names: dict[str, str] = dict(existing_cat_names or {})
    for cat_id in CATEGORIES:
        if not faq_by_cat.get(cat_id):
            continue
        if cat_id in cat_names:
            used.add(cat_names[cat_id])
        else:
            cat_names[cat_id] = category_file_name(cat_id, used)
    for faq in merged_faqs:
        if faq.file_name:
            used.add(faq.file_name)
        else:
            faq.file_name = faq_file_name(faq.question, used)
    for cid, concept in concepts.items():
        if concept.file_name:
            used.add(concept.file_name)
        else:
            concept.file_name = concept_file_name(concept.title, cid, used)
        concept.related_faq_names = []
        if cid not in CONCEPT_DEFS:
            continue
        for faq in merged_faqs:
            blob = faq.question + faq.answer + " ".join(b.body[:300] for b in faq.link_blocks)
            patterns = CONCEPT_DEFS[cid][2]
            if any(re.search(p, blob, re.I) for p in patterns):
                if faq.file_name not in concept.related_faq_names:
                    concept.related_faq_names.append(faq.file_name)
    return cat_names


def session_to_text(data: dict, max_chars: int = 12000) -> str:
    lines = [f"{r}: {c}" for r, c in data["transcript"]]
    if data.get("link_section"):
        lines.append("\n[链接与附件内容]\n" + data["link_section"][:6000])
    return "\n".join(lines)[:max_chars]


def clean_link_body(text: str) -> str:
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("_fetched_at:") or s.startswith("## 图片文字识别"):
            continue
        if s.startswith("_未识别"):
            continue
        if s in ("​",):
            continue
        lines.append(s)
    body = "\n".join(lines).strip()
    return body


def parse_link_section(link_section: str) -> list[LinkBlock]:
    """解析 enrich_sessions 写入的 ## 链接与附件内容。"""
    if not link_section or not link_section.strip():
        return []

    blocks: list[LinkBlock] = []
    current_kind = "网页"
    current_title = ""
    current_url = ""
    buf: list[str] = []

    def flush() -> None:
        nonlocal current_title, current_url, current_kind, buf
        body = clean_link_body("\n".join(buf))
        url = current_url or current_title
        if url.startswith("http") and is_meaningful_text(body, min_chars=20):
            blocks.append(
                LinkBlock(
                    url=url if url.startswith("http") else current_url,
                    title=current_title or url,
                    body=body[:LINK_BODY_MAX_FAQ],
                    kind=current_kind,
                )
            )
        buf = []

    for line in link_section.splitlines():
        header = re.match(r"^### \[(网页|图片 OCR)\]\s*(.+)$", line.strip())
        if header:
            flush()
            current_kind = header.group(1)
            current_title = header.group(2).strip()
            current_url = current_title if current_title.startswith("http") else ""
            continue
        if line.strip().startswith("来源:"):
            current_url = line.strip().replace("来源:", "").strip()
            continue
        if line.strip().startswith("#"):
            continue
        buf.append(line)

    flush()
    return blocks


def steps_from_link_blocks(blocks: list[LinkBlock]) -> list[str]:
    steps: list[str] = []
    for block in blocks:
        for line in block.body.splitlines():
            s = line.strip()
            if len(s) < 6:
                continue
            if STEP_HINTS.search(s) and s not in steps:
                steps.append(s)
    return steps[:12]


def merge_link_blocks(blocks_list: list[LinkBlock]) -> list[LinkBlock]:
    by_url: dict[str, LinkBlock] = {}
    for block in blocks_list:
        key = block.url or block.title
        if not key:
            continue
        if key not in by_url or len(block.body) > len(by_url[key].body):
            by_url[key] = block
    return list(by_url.values())


def render_link_blocks_section(blocks: list[LinkBlock], max_body: int = LINK_BODY_MAX_FAQ) -> str:
    if not blocks:
        return ""
    md = "\n## 链接与附件内容（会话内解析）\n\n"
    for block in blocks:
        md += f"### [{block.kind}] {block.title}\n\n"
        if block.url:
            md += f"来源: {block.url}\n\n"
        excerpt = block.body[:max_body].strip()
        if excerpt:
            md += excerpt + "\n\n"
    return md


def extract_heuristic(data: dict) -> dict | None:
    visitor = substantive_messages(data["transcript"], "访客")
    agent = substantive_messages(data["transcript"], "客服")
    if not visitor or not agent:
        return None

    question = visitor[0]
    if is_noise(question) or len(question) < 4:
        return None
    if re.search(r"的电话号码为|成功购买|成功开通|在.+区域成功", question):
        return None

    answer_parts, steps = [], []
    for msg in agent:
        if extract_urls_from_text(msg) or len(re.findall(r"[\u4e00-\u9fff]", msg)) >= 8:
            answer_parts.append(msg)
        if re.search(r"(点击|打开|进入|选择|第一步|在.+?里|按照)", msg):
            steps.append(msg)

    if not answer_parts:
        answer_parts = agent[-2:] if len(agent) >= 2 else agent
    answer = "\n\n".join(dict.fromkeys(answer_parts))

    link_blocks = parse_link_section(data.get("link_section", ""))
    link_steps = steps_from_link_blocks(link_blocks)
    for s in link_steps:
        if s not in steps:
            steps.append(s)

    if len(answer) < 10 and not link_blocks:
        return None

    all_text = question + " " + answer + " " + data.get("link_section", "")
    cat_id = infer_category_id(all_text)
    links = list(dict.fromkeys(extract_urls_from_text(all_text) + [b.url for b in link_blocks if b.url]))[:10]

    return {
        "skip": False,
        "category_id": cat_id,
        "question": question.rstrip("？?") + "？",
        "question_variants": visitor[1:4],
        "answer": answer,
        "steps": steps[:10],
        "links": links,
        "link_blocks": link_blocks,
    }


def merge_faq_group(group: list[FaqItem]) -> FaqItem:
    primary = max(group, key=lambda x: (len(x.answer), x.source_count))
    variants: list[str] = []
    steps: list[str] = []
    links: list[str] = []
    tags: list[str] = []
    related_wikilinks: list[str] = []
    count = 0
    for g in group:
        count += g.source_count
        for v in [g.question, *g.variants]:
            if v and v not in variants:
                variants.append(v)
        for s in g.steps:
            if s not in steps:
                steps.append(s)
        for u in g.links:
            if u not in links:
                links.append(u)
        for t in g.tags:
            if t not in tags:
                tags.append(t)
        for link in g.related_wikilinks:
            if link and link not in related_wikilinks:
                related_wikilinks.append(link)
    best_answer = max((g.answer for g in group), key=len)
    merged_link_blocks = merge_link_blocks([lb for g in group for lb in g.link_blocks])
    for u in [b.url for b in merged_link_blocks if b.url]:
        if u not in links:
            links.append(u)
    preserved_name = next((g.file_name for g in group if g.file_name), "")
    return FaqItem(
        question=primary.question,
        answer=best_answer,
        category_id=primary.category_id,
        variants=[v for v in variants if v != primary.question][:12],
        steps=steps[:10],
        links=links[:10],
        link_blocks=merged_link_blocks,
        tags=tags[:8],
        related_wikilinks=related_wikilinks[:12],
        source_count=count,
        file_name=preserved_name,
    )


def cluster_merge_faqs(raw_faqs: dict[str, FaqItem], similarity_threshold: float = SIMILARITY_THRESHOLD) -> list[FaqItem]:
    items = list(raw_faqs.values())
    n = len(items)
    if n == 0:
        return []
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    cross_cat_threshold = max(0.82, similarity_threshold + 0.12)
    for i in range(n):
        for j in range(i + 1, n):
            sim = question_similarity(items[i].question, items[j].question)
            if items[i].category_id == items[j].category_id:
                if sim >= similarity_threshold:
                    union(i, j)
            elif sim >= cross_cat_threshold:
                union(i, j)

    groups: dict[int, list[FaqItem]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(items[i])
    return [merge_faq_group(g) for g in groups.values()]


def collect_raw_faqs(extractions: list[tuple[dict, dict]]) -> dict[str, FaqItem]:
    faqs: dict[str, FaqItem] = {}
    for parsed, ext in extractions:
        if ext.get("skip"):
            continue
        cat_id = ext.get("category_id") or infer_category_id(ext.get("question", "") + ext.get("answer", ""))
        question = ext.get("question", "").strip()
        answer = ext.get("answer", "").strip()
        link_blocks = ext.get("link_blocks") or parse_link_section(parsed.get("link_section", ""))
        if not question or (len(answer) < 12 and not link_blocks):
            continue
        qkey = normalize_question(question)[:80]
        links = list(
            dict.fromkeys((ext.get("links") or []) + [b.url for b in link_blocks if b.url])
        )[:10]
        steps = [s for s in ext.get("steps", []) if s and len(s) >= 5]
        for s in steps_from_link_blocks(link_blocks):
            if s not in steps:
                steps.append(s)

        related = [str(x).strip() for x in (ext.get("related_wikilinks") or ext.get("related_faqs") or []) if x]
        tags = [str(t).strip() for t in ext.get("tags", []) if t]
        item = FaqItem(
            question=question,
            answer=answer,
            category_id=cat_id,
            variants=[v for v in ext.get("question_variants", []) if v][:5],
            steps=steps,
            links=links,
            link_blocks=link_blocks,
            tags=tags,
            related_wikilinks=related,
        )
        if qkey in faqs:
            faqs[qkey] = merge_faq_group([faqs[qkey], item])
        else:
            faqs[qkey] = item
    return faqs


def extract_concepts(faqs: list[FaqItem]) -> dict[str, ConceptItem]:
    concepts: dict[str, ConceptItem] = {}
    for concept_id, (title, definition, patterns) in CONCEPT_DEFS.items():
        points: list[str] = []
        cat_ids: set[str] = set()
        matched = False
        for faq in faqs:
            link_text = " ".join(b.body[:200] for b in faq.link_blocks)
            blob = faq.question + faq.answer + " ".join(faq.variants) + link_text
            if not any(re.search(p, blob, re.I) for p in patterns):
                continue
            matched = True
            cat_ids.add(faq.category_id)
            snippet = faq.answer.split("\n")[0][:120]
            if snippet and snippet not in points:
                points.append(snippet)
            for block in faq.link_blocks[:2]:
                tip = block.body.split("\n")[0][:100]
                if tip and tip not in points:
                    points.append(tip)
        if matched:
            concepts[concept_id] = ConceptItem(
                concept_id=concept_id,
                title=title,
                definition=definition,
                points=points[:8],
                category_ids=sorted(cat_ids),
            )
    return concepts


def render_faq_file(item: FaqItem, category_link: str, today: str) -> str:
    title = category_title(item.category_id)
    body = f"""---
type: faq
category: "[[{category_link}]]"
tags: [{title}]
source_count: {item.source_count}
updated_at: {today}
---

# {item.question}

## 客户问法

"""
    for v in [item.question, *item.variants]:
        body += f"- {v}\n"

    body += f"\n## 标准回答\n\n{item.answer.strip()}\n"
    body += render_link_blocks_section(item.link_blocks)

    if item.steps:
        body += "\n## 操作步骤\n"
        for i, s in enumerate(item.steps, 1):
            body += f"{i}. {s}\n"
    if item.links:
        body += "\n## 参考链接\n"
        for u in item.links:
            body += f"- {u}\n"
    body += f"\n## 相关\n- [[{category_link}]]\n"
    for link in item.related_wikilinks[:12]:
        link = link.strip().removesuffix(".md")
        if link:
            body += f"- [[{link}]]\n"
    return body


def render_category_file(
    cat_id: str,
    category_link: str,
    faqs: list[FaqItem],
    concepts: list[ConceptItem],
    cat_name_map: dict[str, str],
    today: str,
) -> str:
    title, overview, _ = CATEGORIES[cat_id]
    faqs_sorted = sorted(faqs, key=lambda x: -x.source_count)

    body = f"""---
type: category
category_id: {cat_id}
faq_count: {len(faqs_sorted)}
updated_at: {today}
---

# {title}

## 主题概述

{overview}

本主题共整理 **{len(faqs_sorted)}** 条客户问答（相似问题已合并），**{sum(1 for f in faqs_sorted if f.steps)}** 条含操作步骤。

"""

    cat_concepts = [c for c in concepts if cat_id in c.category_ids]
    if cat_concepts:
        body += "## 相关产品概念\n\n"
        for c in cat_concepts:
            body += f"- **[[{c.file_name}]]**：{c.definition}\n"
        body += "\n"

    body += "## 主题内问答总结\n\n"
    for idx, faq in enumerate(faqs_sorted, 1):
        body += f"### {idx}. {faq.question}\n\n"
        body += f"> 详情页：[[{faq.file_name}]]（合并来源 {faq.source_count} 条会话）\n\n"
        if faq.variants:
            body += "**常见问法：**\n"
            for v in faq.variants[:6]:
                body += f"- {v}\n"
            body += "\n"
        body += f"**标准回答：**\n\n{faq.answer.strip()}\n\n"
        if faq.link_blocks:
            body += "**链接与附件（会话内解析摘录）：**\n\n"
            for block in faq.link_blocks[:3]:
                body += f"- [{block.kind}] {block.title}\n"
                if block.url:
                    body += f"  - 来源: {block.url}\n"
                excerpt = block.body[:LINK_BODY_MAX_SUMMARY].strip()
                if excerpt:
                    preview = excerpt.replace("\n", " ")[:280]
                    body += f"  - 摘录: {preview}…\n" if len(excerpt) > 280 else f"  - 摘录: {preview}\n"
            body += "\n"
        if faq.steps:
            body += "**操作步骤：**\n"
            for i, s in enumerate(faq.steps, 1):
                body += f"{i}. {s}\n"
            body += "\n"
        if faq.links:
            body += "**参考链接：** " + "、".join(f"`{u}`" for u in faq.links[:4]) + "\n\n"
        body += "---\n\n"

    proc_faqs = [f for f in faqs_sorted if len(f.steps) >= 2]
    if proc_faqs:
        body += "## 操作流程汇总\n\n"
        seen_steps: set[str] = set()
        for faq in proc_faqs[:20]:
            key = faq.steps[0][:40]
            if key in seen_steps:
                continue
            seen_steps.add(key)
            body += f"### {faq.question.rstrip('？?')}\n"
            for i, s in enumerate(faq.steps, 1):
                body += f"{i}. {s}\n"
            body += "\n"

    body += "## 本主题 FAQ 索引\n\n"
    for faq in faqs_sorted:
        body += f"- [[{faq.file_name}]]\n"

    return body


def render_concept(c: ConceptItem, cat_name_map: dict[str, str], today: str) -> str:
    body = f"""---
type: concept
concept_id: {c.concept_id}
updated_at: {today}
---

# {c.title}

## 定义

{c.definition}

## 从客户问答中归纳的要点

"""
    for p in c.points:
        body += f"- {p}\n"
    if not c.points:
        body += "- （见下方关联问答）\n"

    body += "\n## 关联问答\n"
    for name in c.related_faq_names[:20]:
        body += f"- [[{name}]]\n"

    body += "\n## 相关主题\n"
    for cid in c.category_ids:
        link = cat_name_map.get(cid, f"分类-{cid}")
        body += f"- [[{link}]]\n"
    return body


def render_index(
    faq_by_cat: dict[str, list[FaqItem]],
    concepts: dict[str, ConceptItem],
    cat_name_map: dict[str, str],
    stats: dict,
    today: str,
) -> str:
    lines = [
        "# 智能客服知识库",
        "",
        f"> 按业务主题分类的客户知识索引。更新：{today}",
        "",
        "## 使用说明",
        "",
        "1. 从下方 **主题分类** 进入，主题页内含该领域全部 FAQ 总结与操作流程",
        "2. 单条 FAQ 见 `wiki/faqs/`（相似问题已合并）",
        "3. 产品概念见 `wiki/concepts/`（由 FAQ 归纳）",
        "",
        "## 统计",
        "",
        f"- 主题分类：**{stats['categories']}** 个",
        f"- 客户问答（合并后）：**{stats['faqs']}** 条",
        f"- 产品概念：**{stats['concepts']}** 个",
        f"- 原始会话（仅 raw）：{stats['raw_sessions']} 条",
        "",
        "## 主题分类",
        "",
    ]
    for cat_id, (title, overview, _) in CATEGORIES.items():
        faqs = faq_by_cat.get(cat_id, [])
        if not faqs:
            continue
        lines.append(f"### {title}")
        lines.append("")
        lines.append(overview)
        lines.append("")
        lines.append(f"- 主题页（含全部 FAQ 内总结）：[[{cat_name_map[cat_id]}]]")
        lines.append(f"- 本主题 FAQ 约 **{len(faqs)}** 条")
        top = sorted(faqs, key=lambda x: -x.source_count)[:5]
        if top:
            lines.append("- 高频问题：")
            for f in top:
                lines.append(f"  - {f.question}")
        lines.append("")

    if concepts:
        lines.append("## 产品概念\n")
        for c in concepts.values():
            lines.append(f"- [[{c.file_name}]]：{c.title}")
    return "\n".join(lines)


def render_overview(
    faq_by_cat: dict[str, list[FaqItem]],
    concepts: dict[str, ConceptItem],
    cat_name_map: dict[str, str],
    stats: dict,
    today: str,
) -> str:
    lines = [
        "# 知识库总览",
        "",
        f"> 更新：{today}",
        "",
        "## 知识结构",
        "",
        "| 层级 | 目录 | 说明 |",
        "|------|------|------|",
        "| 索引 | `index.md` | 主题入口 |",
        "| 主题 | `categories/` | **内含该主题全部 FAQ 总结 + 操作流程** |",
        "| 问答 | `faqs/` | 合并后的单条 FAQ（可深链） |",
        "| 概念 | `concepts/` | **从 FAQ 归纳**的产品/术语说明 |",
        "| 原始 | `raw/chats/` | 会话原文，不入索引 |",
        "",
        f"- 合并后 FAQ：**{stats['faqs']}** 条",
        f"- 概念页：**{stats['concepts']}** 个",
        "",
        "## 主题分布",
        "",
    ]
    for cat_id, (title, _, _) in CATEGORIES.items():
        n = len(faq_by_cat.get(cat_id, []))
        if n:
            lines.append(f"- **{title}**：{n} 条 FAQ → [[{cat_name_map[cat_id]}]]")
    return "\n".join(lines)


def clear_wiki(wiki_dir: Path) -> None:
    for sub in ("faqs", "concepts", "categories"):
        d = wiki_dir / sub
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    proc = wiki_dir / "procedures"
    if proc.exists():
        shutil.rmtree(proc)


def ensure_wiki_dirs(wiki_dir: Path) -> None:
    for sub in ("faqs", "concepts", "categories"):
        (wiki_dir / sub).mkdir(parents=True, exist_ok=True)


def collect_used_names(wiki_dir: Path) -> set[str]:
    used: set[str] = set()
    for sub in ("faqs", "concepts", "categories"):
        d = wiki_dir / sub
        if d.is_dir():
            used.update(p.stem for p in d.glob("*.md"))
    return used


def load_existing_category_names(wiki_dir: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    cat_dir = wiki_dir / "categories"
    if not cat_dir.is_dir():
        return mapping
    for path in cat_dir.glob("*.md"):
        m = re.search(r"^category_id:\s*(\S+)", path.read_text(encoding="utf-8")[:500], re.M)
        if m:
            mapping[m.group(1).strip()] = path.stem
    return mapping


def parse_faq_markdown(path: Path) -> FaqItem | None:
    text = path.read_text(encoding="utf-8")
    if "type: faq" not in text[:400] and "## 客户问法" not in text:
        return None
    m_q = re.search(r"^# (.+)$", text, re.M)
    if not m_q:
        return None
    question = m_q.group(1).strip()
    variants: list[str] = []
    in_variants = False
    for line in text.splitlines():
        if line.strip() == "## 客户问法":
            in_variants = True
            continue
        if in_variants and line.startswith("## "):
            break
        if in_variants and line.startswith("- "):
            v = line[2:].strip()
            if v:
                variants.append(v)
    m_ans = re.search(r"## 标准回答\s*\n+([\s\S]*?)(?=\n## |\Z)", text)
    answer = m_ans.group(1).strip() if m_ans else ""
    m_links = re.search(r"## 链接与附件内容[^\n]*\n+([\s\S]*?)(?=\n## |\Z)", text)
    link_blocks = parse_link_section(m_links.group(1)) if m_links else []
    steps: list[str] = []
    m_steps = re.search(r"## 操作步骤\s*\n+([\s\S]*?)(?=\n## |\Z)", text)
    if m_steps:
        for line in m_steps.group(1).splitlines():
            line = re.sub(r"^\d+\.\s*", "", line.strip())
            if line:
                steps.append(line)
    links = re.findall(r"^-\s+(https?://\S+)", text, re.M)
    sc = re.search(r"^source_count:\s*(\d+)", text, re.M)
    source_count = int(sc.group(1)) if sc else 1
    cat_id = infer_category_id(question + answer)
    return FaqItem(
        question=question,
        answer=answer,
        category_id=cat_id,
        variants=[v for v in variants if v != question],
        steps=steps,
        links=links[:10],
        link_blocks=link_blocks,
        source_count=source_count,
        file_name=path.stem,
    )


def load_existing_faqs(wiki_dir: Path) -> list[FaqItem]:
    faqs_dir = wiki_dir / "faqs"
    if not faqs_dir.is_dir():
        return []
    items: list[FaqItem] = []
    for path in sorted(faqs_dir.glob("*.md")):
        item = parse_faq_markdown(path)
        if item:
            items.append(item)
    return items


def parse_concept_markdown(path: Path) -> ConceptItem | None:
    text = path.read_text(encoding="utf-8")
    m_id = re.search(r"^concept_id:\s*(\S+)", text, re.M)
    if not m_id:
        return None
    concept_id = m_id.group(1).strip()
    if concept_id in CONCEPT_DEFS:
        title, definition, _ = CONCEPT_DEFS[concept_id]
    else:
        m_title = re.search(r"^# (.+)$", text, re.M)
        title = m_title.group(1).strip() if m_title else concept_id
        m_def = re.search(r"## 定义\s*\n+([\s\S]*?)(?=\n## |\Z)", text)
        definition = m_def.group(1).strip() if m_def else title
    points: list[str] = []
    in_points = False
    for line in text.splitlines():
        if line.strip() == "## 从客户问答中归纳的要点":
            in_points = True
            continue
        if in_points and line.startswith("## "):
            break
        if in_points and line.startswith("- "):
            points.append(line[2:].strip())
    return ConceptItem(
        concept_id=concept_id,
        title=title,
        definition=definition,
        file_name=path.stem,
        points=points[:8],
    )


def load_existing_concepts(wiki_dir: Path) -> dict[str, ConceptItem]:
    concepts_dir = wiki_dir / "concepts"
    if not concepts_dir.is_dir():
        return {}
    out: dict[str, ConceptItem] = {}
    for path in concepts_dir.glob("*.md"):
        item = parse_concept_markdown(path)
        if item:
            out[item.concept_id] = item
    return out


def merge_new_into_existing(
    existing: list[FaqItem],
    new_raw: dict[str, FaqItem],
    similarity_threshold: float,
) -> list[FaqItem]:
    pool = list(existing)
    for item in new_raw.values():
        match: FaqItem | None = None
        best_sim = similarity_threshold
        for ex in pool:
            sim = question_similarity(item.question, ex.question)
            if sim >= best_sim:
                best_sim, match = sim, ex
        if match is None:
            nk = normalize_question(item.question)[:80]
            for ex in pool:
                if normalize_question(ex.question)[:80] == nk:
                    match = ex
                    break
        if match:
            idx = pool.index(match)
            pool[idx] = merge_faq_group([pool[idx], item])
        else:
            pool.append(item)
    return pool


def merge_concept_dicts(
    existing: dict[str, ConceptItem],
    extracted: dict[str, ConceptItem],
) -> dict[str, ConceptItem]:
    out = dict(existing)
    for cid, new_c in extracted.items():
        if cid not in out:
            out[cid] = new_c
            continue
        old = out[cid]
        points = list(old.points)
        for p in new_c.points:
            if p and p not in points:
                points.append(p)
        cat_ids = sorted(set(old.category_ids) | set(new_c.category_ids))
        out[cid] = ConceptItem(
            concept_id=cid,
            title=new_c.title,
            definition=new_c.definition,
            file_name=old.file_name or new_c.file_name,
            points=points[:8],
            category_ids=cat_ids,
        )
    return out


def _session_preview(path: Path) -> dict:
    data = parse_session_file(path)
    return {"preview": session_to_text(data)[:400]}


def main() -> None:
    from agent_ingest import export_session_queue, load_session_extractions

    parser = argparse.ArgumentParser(
        description="客服会话 → wiki（规则合并；大模型萃取由上层 Agent 完成，脚本不调 API）"
    )
    parser.add_argument("--chats-dir", type=Path, required=True)
    parser.add_argument("--wiki-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--mode",
        choices=("heuristic", "agent"),
        default="heuristic",
        help="heuristic=规则萃取；agent=读取 Agent 写入的 extractions/*.json",
    )
    parser.add_argument(
        "--extractions-dir",
        type=Path,
        default=None,
        help="Agent 萃取结果目录，如 wiki/.agent-queue/extractions/",
    )
    parser.add_argument(
        "--export-queue",
        type=Path,
        default=None,
        help="仅导出待 Agent 处理的会话清单到该目录（写入 manifest.json + PROMPT.md）",
    )
    parser.add_argument("--similarity", type=float, default=SIMILARITY_THRESHOLD)
    parser.add_argument(
        "--full-rebuild",
        action="store_true",
        help="清空 wiki/faqs、concepts、categories 后全量重建（默认增量：相同更新、新增写入、不删已有）",
    )
    args = parser.parse_args()

    if args.export_queue:
        n = export_session_queue(args.chats_dir, args.export_queue, _session_preview, args.limit)
        print(f"Exported agent queue: {n} pending sessions -> {args.export_queue}")
        print("由上层 Agent 按 PROMPT.md 萃取并写入 extractions/<session_id>.json 后，再运行 --extractions-dir 合并。")
        return

    extractions_dir = args.extractions_dir
    if args.mode == "agent" and not extractions_dir:
        extractions_dir = args.wiki_dir / ".agent-queue" / "extractions"

    if args.full_rebuild:
        clear_wiki(args.wiki_dir)
        existing_faqs: list[FaqItem] = []
        existing_concepts: dict[str, ConceptItem] = {}
    else:
        ensure_wiki_dirs(args.wiki_dir)
        existing_faqs = load_existing_faqs(args.wiki_dir)
        existing_concepts = load_existing_concepts(args.wiki_dir)

    files = sorted(args.chats_dir.glob("session_*.md"))
    if args.limit > 0:
        files = files[: args.limit]

    extractions: list[tuple[dict, dict]] = []
    skipped = 0

    if args.mode == "agent" and extractions_dir and extractions_dir.is_dir():
        extractions = load_session_extractions(
            extractions_dir,
            args.chats_dir,
            parse_session_file,
            parse_link_section,
            infer_category_id,
            categories=None,
        )
        skipped = len(files) - len(extractions)
        print(f"Loaded {len(extractions)} agent extractions from {extractions_dir}")
    else:
        for path in files:
            data = parse_session_file(path)
            try:
                ext = extract_heuristic(data)
            except (json.JSONDecodeError, KeyError, IndexError, Exception):
                skipped += 1
                continue
            if not ext or ext.get("skip"):
                skipped += 1
                continue
            extractions.append((data, ext))

    raw_faqs = collect_raw_faqs(extractions)
    pool = merge_new_into_existing(existing_faqs, raw_faqs, args.similarity)
    pool_dict = {f"faq-{i}": f for i, f in enumerate(pool)}
    merged_faqs = cluster_merge_faqs(pool_dict, args.similarity)
    extracted_concepts = extract_concepts(merged_faqs)
    concepts = extracted_concepts if args.full_rebuild else merge_concept_dicts(existing_concepts, extracted_concepts)

    today = date.today().isoformat()
    faq_by_cat: dict[str, list[FaqItem]] = defaultdict(list)
    for faq in merged_faqs:
        faq_by_cat[faq.category_id].append(faq)

    reserved = collect_used_names(args.wiki_dir)
    existing_cats = load_existing_category_names(args.wiki_dir)
    cat_name_map = assign_all_file_names(
        merged_faqs, concepts, faq_by_cat, reserved_names=reserved, existing_cat_names=existing_cats
    )

    for faq in merged_faqs:
        cat_link = cat_name_map[faq.category_id]
        (args.wiki_dir / "faqs" / f"{faq.file_name}.md").write_text(
            render_faq_file(faq, cat_link, today), encoding="utf-8"
        )

    for c in concepts.values():
        (args.wiki_dir / "concepts" / f"{c.file_name}.md").write_text(
            render_concept(c, cat_name_map, today), encoding="utf-8"
        )

    for cat_id in CATEGORIES:
        cat_faqs = faq_by_cat.get(cat_id, [])
        if not cat_faqs:
            continue
        (args.wiki_dir / "categories" / f"{cat_name_map[cat_id]}.md").write_text(
            render_category_file(cat_id, cat_name_map[cat_id], cat_faqs, list(concepts.values()), cat_name_map, today),
            encoding="utf-8",
        )

    stats = {
        "faqs": len(merged_faqs),
        "concepts": len(concepts),
        "categories": len(cat_name_map),
        "raw_sessions": len(files),
    }
    (args.wiki_dir / "index.md").write_text(
        render_index(faq_by_cat, concepts, cat_name_map, stats, today), encoding="utf-8"
    )
    (args.wiki_dir / "overview.md").write_text(
        render_overview(faq_by_cat, concepts, cat_name_map, stats, today), encoding="utf-8"
    )

    log = args.wiki_dir / "log.md"
    mode_label = "全量重建" if args.full_rebuild else "增量更新"
    ingest_mode = "agent" if args.mode == "agent" and extractions_dir else "heuristic"
    entry = (
        f"\n## {today} wiki {mode_label} ({ingest_mode})\n"
        f"- 合并后 FAQ: {stats['faqs']}，概念: {stats['concepts']}，跳过: {skipped}\n"
        f"- 策略：相同问法更新、新问法新增，**不删除** wiki 已有条目\n"
        f"- 主题页含 FAQ 内总结 + 操作流程；无独立 procedures 目录\n"
    )
    log.write_text((log.read_text(encoding="utf-8") if log.exists() else "# 操作日志\n") + entry, encoding="utf-8")

    print(
        f"Done ({mode_label}). faqs={stats['faqs']} concepts={stats['concepts']} "
        f"categories={stats['categories']} skipped={skipped}"
    )


if __name__ == "__main__":
    main()
