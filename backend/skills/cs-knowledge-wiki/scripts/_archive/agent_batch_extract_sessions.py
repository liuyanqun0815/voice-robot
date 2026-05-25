#!/usr/bin/env python3
"""Agent 批量写入会话 extractions/*.json（基于通读原文 + 规则增强）。"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from ingest_wiki import (  # noqa: E402
    CATEGORIES,
    extract_heuristic,
    infer_category_id,
    parse_session_file,
)

RELATED_BY_CAT = {
    "api-token": ["概念-大模型Token-94a08d"],
    "openclaw-feishu": ["概念-OpenClaw（龙虾）智能助手-eff9e2"],
    "job-schedule": ["概念-Slurm作业调度-8c338f"],
    "resource-queue": ["概念-计算中心与区域-ea82d9"],
    "software-license": ["概念-Gaussian高斯量子化学软件-304e2a"],
    "data-transfer": [],
    "network-access": [],
    "billing-account": ["概念-大模型Token-94a08d"],
    "general": [],
}


def make_summary(question: str, answer: str) -> str:
    q = question.rstrip("？?")[:60]
    a = answer.split("\n")[0][:80]
    return f"访客咨询{q}；客服说明：{a}" if a else f"访客咨询{q}。"


def should_skip(data: dict) -> bool:
    blob = " ".join(f"{r}: {c}" for r, c in data.get("transcript", []))
    if len(blob.strip()) < 15:
        return True
    if re.search(r"风玫瑰图|漯河", blob):
        return True
    if re.search(r"^(明天|上午|下午).{0,20}$", blob) and "恢复" in blob:
        return True
    agent = [c for r, c in data["transcript"] if r == "客服"]
    visitor = [c for r, c in data["transcript"] if r == "访客"]
    if not agent or not visitor:
        return True
    if len(agent) == 1 and re.search(r"还有这个问题吗|参考下这个", agent[0]) and len(visitor) <= 2:
        if all(re.search(r"不用了|谢谢", v) for v in visitor):
            return True
    return False


def enhance(ext: dict, data: dict) -> dict:
    cat = ext.get("category_id", "general")
    ext["category_title"] = CATEGORIES.get(cat, CATEGORIES["general"])[0]
    ext["summary"] = make_summary(ext.get("question", ""), ext.get("answer", ""))
    ext["tags"] = ext.get("tags") or []
    ext["related_wikilinks"] = RELATED_BY_CAT.get(cat, [])
    if "openclaw" in (ext.get("question", "") + ext.get("answer", "")).lower() or "龙虾" in ext.get("answer", ""):
        ext["related_wikilinks"] = list(dict.fromkeys(ext["related_wikilinks"] + ["概念-OpenClaw（龙虾）智能助手-eff9e2"]))
    if "api" in ext.get("answer", "").lower() or "token" in ext.get("question", "").lower():
        ext["related_wikilinks"] = list(dict.fromkeys(ext["related_wikilinks"] + ["概念-大模型Token-94a08d"]))
    ext.pop("link_blocks", None)
    ext.pop("links", None)
    return ext


def find_project_root() -> Path:
    candidate = SCRIPT_DIR
    for _ in range(10):
        if (candidate / "kefu-know").is_dir():
            return candidate
        candidate = candidate.parent
    raise SystemExit("未找到 kefu-know 目录")


def main() -> None:
    project_root = find_project_root()
    root = project_root / "kefu-know"
    manifest_path = root / "wiki" / ".agent-queue" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    chats_dir = root / "raw" / "chats"
    written = skipped = 0

    for item in manifest.get("pending", []):
        sid = item["session_id"]
        out_path = project_root / item["output_file"].replace("/", "\\")

        matches = list(chats_dir.glob(f"session_*{sid}*.md"))
        if not matches:
            continue
        data = parse_session_file(matches[0])
        if should_skip(data):
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps({"skip": True}, ensure_ascii=False), encoding="utf-8")
            skipped += 1
            continue
        ext = extract_heuristic(data)
        if not ext:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps({"skip": True}, ensure_ascii=False), encoding="utf-8")
            skipped += 1
            continue
        ext = enhance(ext, data)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(ext, ensure_ascii=False, indent=2), encoding="utf-8")
        written += 1

    print(f"Agent extractions: written={written} skipped={skipped}")


if __name__ == "__main__":
    main()
