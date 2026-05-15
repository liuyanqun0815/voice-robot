import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.services.tts.volcano_ws_client import VolcanoTtsClient


def test_volcano_tts_stream_returns_chunk_iterable() -> None:
    client = VolcanoTtsClient()

    async def _collect() -> list[bytes]:
        result: list[bytes] = []
        async for chunk in client.stream_sentence("你好，这是测试语音。"):
            result.append(chunk)
        return result

    chunks = asyncio.run(_collect())

    assert len(chunks) >= 1
