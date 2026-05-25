#!/usr/bin/env python3
"""Agent 辅助：根据 URL 路径规则为全部 raw 网站文档生成 taxonomy.json（分类+分配+摘要）。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ingest_websites import parse_website_file


def assign_category_id(url: str) -> str:
    u = (url or "").lower()
    if "/ac/openapi/doc/" in u:
        if "/notebook" in u:
            return "api-notebook-rest"
        if "/codingplan" in u or "/moduleapi/tutorial" in u or "/moduleapi/" in u and "llm" in u:
            return "ai-maas-openapi"
        if "/job" in u or "slurm" in u or "/hpc/" in u:
            return "api-job-resource"
        return "api-general"
    if "/help/docs/" in u:
        if "/openclaw/" in u or "/sclaw/" in u:
            return "openclaw-integration"
        if "/notebook/" in u or "/ai/practice/" in u:
            return "ai-notebook"
        if "/aihub/mcp" in u or "/aihub/studios" in u:
            return "aihub-mcp-studio"
        if "/bw-core-node/" in u or "das-introduction" in u or "dtk-introduction" in u or "/ai4s" in u:
            return "ai-platform-dcu"
        if "/software-examples/use/cae" in u or "/use/cae/" in u:
            return "hpc-cae-software"
        if "/software-examples/" in u:
            return "hpc-science-software"
        if "/beginner-guide/" in u or "/noun-" in u or "introduction" in u:
            return "hpc-beginner-guide"
        return "platform-intro"
    return "platform-intro"


def make_summary(title: str, content: str) -> str:
    title = (title or "").strip()
    first = ""
    for line in (content or "").split("\n"):
        line = line.strip()
        if len(line) >= 20 and not line.startswith("#"):
            first = line[:200]
            break
    if first:
        return f"{title}：{first[:120]}…" if len(first) > 120 else f"{title}：{first}"
    return f"{title}：平台帮助文档，介绍相关功能与操作步骤。"


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir
    for _ in range(10):
        if (project_root / "kefu-know").is_dir():
            break
        project_root = project_root.parent
    else:
        raise SystemExit("未找到 kefu-know 目录")
    root = project_root / "kefu-know"
    websites_root = root / "raw" / "websites"
    out_path = root / "wiki" / "taxonomy.json"

    docs: list[dict] = []
    for path in sorted(websites_root.rglob("*.md")):
        try:
            data = parse_website_file(path)
            if not data.get("title") or len(data.get("content", "")) < 80:
                continue
            url = data.get("source_url", "")
            docs.append(
                {
                    "title": data["title"],
                    "content": data["content"],
                    "source_url": url,
                    "source_site": path.parent.name,
                }
            )
        except Exception:
            pass

    seen: dict[str, dict] = {}
    for d in docs:
        key = d["source_url"] or d["title"]
        if key not in seen:
            seen[key] = d
    docs = list(seen.values())

    categories = {
        "hpc-science-software": {
            "id": "hpc-science-software",
            "title": "科学计算软件与最佳实践",
            "overview": "分子动力学、第一性原理、生信等科学软件的使用说明与实操案例。",
        },
        "hpc-cae-software": {
            "id": "hpc-cae-software",
            "title": "CAE 仿真软件",
            "overview": "Ansys、Fluent、ABAQUS、COMSOL 等结构/流体/电磁仿真软件在平台上的提交与使用。",
        },
        "hpc-beginner-guide": {
            "id": "hpc-beginner-guide",
            "title": "HPC 入门与名词解释",
            "overview": "新手指南、文件上传、作业提交入门及 HPC 基础概念。",
        },
        "ai-notebook": {
            "id": "ai-notebook",
            "title": "Notebook 与 AI 应用",
            "overview": "Notebook 功能、镜像保存、自定义服务端口及图生图等 AI 应用实践。",
        },
        "ai-maas-openapi": {
            "id": "ai-maas-openapi",
            "title": "大模型 API 与 Coding Plan",
            "overview": "OpenAI/Anthropic 协议调用、API Key、Coding Plan 订阅与接入开发工具。",
        },
        "ai-platform-dcu": {
            "id": "ai-platform-dcu",
            "title": "国产加速与 AI4S 平台",
            "overview": "核心节点、DAS/DTK、AI4S/AI 类应用及异构加速资源说明。",
        },
        "openclaw-integration": {
            "id": "openclaw-integration",
            "title": "OpenClaw 与渠道集成",
            "overview": "OpenClaw（龙虾）部署及飞书、微信等渠道接入配置。",
        },
        "api-notebook-rest": {
            "id": "api-notebook-rest",
            "title": "Notebook OpenAPI",
            "overview": "Notebook 实例创建、启停、镜像与 Jupyter 地址查询等 REST 接口。",
        },
        "api-job-resource": {
            "id": "api-job-resource",
            "title": "作业与资源 OpenAPI",
            "overview": "作业提交、队列与资源查询相关开放接口。",
        },
        "api-general": {
            "id": "api-general",
            "title": "其他开放 API",
            "overview": "未归入专题的开放平台接口文档。",
        },
        "aihub-mcp-studio": {
            "id": "aihub-mcp-studio",
            "title": "AI Hub / MCP / Studio",
            "overview": "MCP 协议、Studio API 及 ArXiv 等工具实践。",
        },
        "platform-intro": {
            "id": "platform-intro",
            "title": "平台介绍与通用帮助",
            "overview": "站点介绍、通用说明及其他帮助文档。",
        },
    }

    concept_defs = [
        ("slurm", "Slurm", "作业调度系统，用于提交、排队与管理 HPC 作业。", ["slurm", "作业", "sbatch", "队列"]),
        ("gaussian", "Gaussian", "量子化学计算软件 Gaussian 的平台使用与许可。", ["gaussian", "高斯"]),
        ("lammps", "LAMMPS", "分子动力学软件 LAMMPS 及加速版使用。", ["lammps"]),
        ("gromacs", "GROMACS", "分子动力学软件 GROMACS。", ["gromacs"]),
        ("openclaw", "OpenClaw", "平台一键部署的智能助手（龙虾）及渠道配置。", ["openclaw", "龙虾", "open claw"]),
        ("notebook", "Notebook", "交互式 Notebook 开发环境与实例管理。", ["notebook", "jupyter"]),
        ("llm-api", "大模型 API", "兼容 OpenAI/Anthropic 的模型调用与 API Key。", ["api", "token", "chat/completions", "coding plan", "模型"]),
        ("ansys", "Ansys 系列", "Fluent/CFX/Mechanical 等 Ansys 仿真软件。", ["ansys", "fluent", "cfx"]),
        ("vasp", "VASP", "第一性原理计算软件 VASP。", ["vasp", "abacus", "第一性原理"]),
        ("conda", "Conda 环境", "集群上 conda 虚拟环境与软件安装。", ["conda", "环境", "pip"]),
        ("dcu-dtk", "DCU / DTK", "国产加速卡软件栈 DAS、DTK 与异构适配。", ["dcu", "dtk", "das", "异构", "加速"]),
        ("mcp", "MCP", "Model Context Protocol 工具服务器与集成。", ["mcp"]),
    ]

    assignments = []
    doc_enrichments = []
    concept_indices: dict[str, list[int]] = {c[0]: [] for c in concept_defs}

    for i, doc in enumerate(docs):
        url = doc["source_url"]
        cid = assign_category_id(url)
        assignments.append({"source_url": url, "category_id": cid})
        summary = make_summary(doc["title"], doc["content"])
        related_concepts = []
        blob = (doc["title"] + doc["content"][:500]).lower()
        for concept_id, _, _, keywords in concept_defs:
            if any(k in blob for k in keywords):
                concept_indices[concept_id].append(i)
                related_concepts.append(concept_id)
        doc_enrichments.append(
            {
                "source_url": url,
                "summary": summary,
                "related_concept_ids": related_concepts[:5],
            }
        )

    concepts = [
        {
            "id": cid,
            "title": title,
            "definition": definition,
            "related_doc_indices": concept_indices[cid][:30],
        }
        for cid, title, definition, _ in concept_defs
        if concept_indices[cid]
    ]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "cursor-agent",
        "categories": list(categories.values()),
        "concepts": concepts,
        "assignments": assignments,
        "doc_enrichments": doc_enrichments,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out_path} docs={len(docs)} categories={len(categories)} concepts={len(concepts)}")


if __name__ == "__main__":
    main()
