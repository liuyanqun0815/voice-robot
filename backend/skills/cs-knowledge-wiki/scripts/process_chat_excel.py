#!/usr/bin/env python3
"""Process customer-service chat Excel: group by session, clean, optional inline link enrich."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

from kb_utils import extract_urls_from_text, is_noise, is_session_meaningful

COL_SESSION = "会话ID"
COL_SENDER_TYPE = "消息发送方类型"
COL_SENDER_TYPE_PREFIX = "消息发送方类型"
COL_CONTENT = "消息内容"
COL_TIME = "createTime"
COL_FILE_URL = "fileUrl"
COL_SENSITIVE = "sensitiveWord"

SENDER_AGENT = 1
SENDER_VISITOR = 2
SENDER_SYSTEM = 3
SENDER_BOT = 4

ROLE_DISPLAY = {
    SENDER_AGENT: "客服",
    SENDER_VISITOR: "访客",
    SENDER_BOT: "机器人",
    SENDER_SYSTEM: "系统",
    5: "机器人",  # chat_20260403.xlsx 等导出格式
}


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "mainUniqueId": COL_SESSION,
        "session_id": COL_SESSION,
        "senderType": COL_SENDER_TYPE,
        "sender_type": COL_SENDER_TYPE,
        "content": COL_CONTENT,
        "createTime": COL_TIME,
        "create_time": COL_TIME,
        "fileUrl": COL_FILE_URL,
        "file_url": COL_FILE_URL,
        "sensitiveWord": COL_SENSITIVE,
    }
    rename = {k: v for k, v in aliases.items() if k in df.columns and v not in df.columns}
    df = df.rename(columns=rename)
    if COL_SENDER_TYPE not in df.columns:
        for col in df.columns:
            if str(col).startswith(COL_SENDER_TYPE_PREFIX):
                df = df.rename(columns={col: COL_SENDER_TYPE})
                break
    return df


def role_display(sender_type: int) -> str:
    return ROLE_DISPLAY.get(sender_type, "其他")


def build_session_md(session_id: str, group: pd.DataFrame) -> tuple[str | None, list[str]]:
    group = group.sort_values(COL_TIME)
    all_urls: list[str] = []
    transcript: list[str] = []

    for _, row in group.iterrows():
        content = str(row.get(COL_CONTENT, "") or "").strip()
        if not content or is_noise(content):
            continue
        if pd.notna(row.get(COL_SENSITIVE)) and str(row[COL_SENSITIVE]).strip():
            continue

        sender_type = int(row.get(COL_SENDER_TYPE, 0) or 0)
        if sender_type in (SENDER_SYSTEM, SENDER_BOT, 5):
            continue
        if sender_type not in (SENDER_AGENT, SENDER_VISITOR):
            continue

        time_str = str(row.get(COL_TIME, ""))
        role = role_display(sender_type)
        transcript.append(f"- [{time_str}] **{role}**: {content}")

        for url in extract_urls_from_text(content):
            all_urls.append(url)

        file_url = row.get(COL_FILE_URL)
        if pd.notna(file_url) and str(file_url).strip().startswith("http"):
            all_urls.append(str(file_url).strip())

    if not is_session_meaningful(transcript):
        return None, []

    time_range = ""
    if COL_TIME in group.columns and len(group):
        times = group[COL_TIME].astype(str)
        time_range = f"{times.min()} ~ {times.max()}"

    unique_urls = list(dict.fromkeys(all_urls))
    fm = f'---\nsource_type: chat\nsession_id: "{session_id}"\nmessage_count: {len(group)}\n'
    if time_range:
        fm += f'time_range: "{time_range}"\n'
    if unique_urls:
        fm += "file_urls:\n" + "".join(f'  - "{u}"\n' for u in unique_urls)
    fm += "---\n\n"

    body = [f"# 会话 {session_id}\n", "## 对话记录\n" + "\n".join(transcript) + "\n"]
    return fm + "\n".join(body), unique_urls


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--enrich-links",
        action="store_true",
        help="Fetch URLs/OCR and write inline under each session (## 链接与附件内容)",
    )
    parser.add_argument("--with-ocr", action="store_true", help="OCR image URLs when --enrich-links")
    parser.add_argument("--min-messages", type=int, default=2)
    parser.add_argument(
        "--append",
        action="store_true",
        help="增量写入：不删除 output 下已有 session_*.md，仅覆盖本批同名会话",
    )
    args = parser.parse_args()

    df = normalize_columns(pd.read_excel(args.input))
    required = [COL_SESSION, COL_CONTENT, COL_TIME]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise SystemExit(f"Missing columns: {missing}. Got: {list(df.columns)}")

    args.output.mkdir(parents=True, exist_ok=True)

    removed = 0
    if not args.append:
        for old in args.output.glob("session_*.md"):
            old.unlink()
            removed += 1

    sessions_written = 0
    sessions_skipped = 0

    for session_id, group in df.groupby(COL_SESSION):
        if len(group) < args.min_messages:
            sessions_skipped += 1
            continue
        md, _urls = build_session_md(str(session_id), group)
        if md is None:
            sessions_skipped += 1
            continue
        safe_id = re.sub(r"[^\w\-]", "-", str(session_id))[:64]
        out = args.output / f"session_{safe_id}.md"
        out.write_text(md, encoding="utf-8")
        sessions_written += 1

    print(
        f"Wrote {sessions_written} session files (removed {removed} old, "
        f"skipped {sessions_skipped} low-value/visitor-only)"
    )

    if args.enrich_links and sessions_written > 0:
        script = Path(__file__).parent / "enrich_sessions.py"
        cmd = [sys.executable, str(script), "--chats-dir", str(args.output)]
        if args.with_ocr:
            cmd.append("--with-ocr")
        subprocess.run(cmd, check=False)


if __name__ == "__main__":
    main()
