"""将模型流式文本按标点切分为适合 TTS 的短句。"""

from __future__ import annotations

# 中英常见句读与停顿符号；过长无标点时由 max_buffer_without_boundary 强制切段。
_BOUNDARY_CHARS: frozenset[str] = frozenset("。！？!?…，、；;：:.," + "''\"\"（）()《》[]【】「」" + "\n\r")


def _first_boundary_index(text: str) -> int:
    for i, ch in enumerate(text):
        if ch in _BOUNDARY_CHARS:
            return i
    return -1


class PunctuationStreamBuffer:
    """增量写入字符，在出现句读或缓冲区过长时产出待合成片段。"""

    def __init__(self, max_buffer_without_boundary: int = 120) -> None:
        self._buffer = ""
        self._max = max_buffer_without_boundary

    def feed(self, chunk: str) -> list[str]:
        self._buffer += chunk
        segments: list[str] = []
        while True:
            if not self._buffer:
                break
            idx = _first_boundary_index(self._buffer)
            if idx >= 0:
                piece = self._buffer[: idx + 1].strip()
                self._buffer = self._buffer[idx + 1 :]
                if piece:
                    segments.append(piece)
                continue
            if len(self._buffer) >= self._max:
                piece = self._buffer[: self._max].strip()
                self._buffer = self._buffer[self._max :]
                if piece:
                    segments.append(piece)
                continue
            break
        return segments

    def drain(self) -> str | None:
        tail = self._buffer.strip()
        self._buffer = ""
        return tail or None
