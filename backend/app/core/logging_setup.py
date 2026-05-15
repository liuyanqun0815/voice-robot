"""统一日志 UTF-8 输出，避免 Windows 控制台中文乱码。"""

from __future__ import annotations

import logging
import sys


def configure_logging(level: int = logging.INFO) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)

    # 腾讯 SDK 自带 logger 会往文件写 GBK/混合编码；统一走 UTF-8 控制台即可读。
    tencent_logger = logging.getLogger("tencent_speech.log")
    tencent_logger.handlers.clear()
    tencent_logger.propagate = True
    tencent_logger.setLevel(level)
