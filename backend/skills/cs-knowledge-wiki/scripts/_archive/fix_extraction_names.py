#!/usr/bin/env python3
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from agent_ingest import session_id_from_path


def find_project_root() -> Path:
    candidate = SCRIPT_DIR
    for _ in range(10):
        if (candidate / "kefu-know").is_dir():
            return candidate
        candidate = candidate.parent
    raise SystemExit("kefu-know not found")


def main() -> None:
    root = find_project_root() / "kefu-know"
    ext_dir = root / "wiki" / ".agent-queue" / "extractions"
    chats = root / "raw" / "chats"
    for p in list(ext_dir.glob("*.json")):
        matches = list(chats.glob(f"session_*{p.stem}*.md"))
        if not matches:
            continue
        sid = session_id_from_path(matches[0])
        new_path = ext_dir / f"{sid}.json"
        if new_path == p:
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        new_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        p.unlink(missing_ok=True)
        print(f"renamed {p.name} -> {new_path.name}")


if __name__ == "__main__":
    main()
