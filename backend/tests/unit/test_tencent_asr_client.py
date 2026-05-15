import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.services.asr.tencent_ws_client import TencentAsrClient


def test_append_audio_buffers_until_ready() -> None:
    client = TencentAsrClient()
    received: list[bytes] = []

    asyncio.run(client.connect("s1"))
    session = client._sessions["s1"]
    session.is_ready = False
    asyncio.run(client.append_audio("s1", b"pending"))
    assert session.pending_audio == [b"pending"]

    client.register_mock_writer("s1", received.append)
    session.is_ready = True
    client._flush_pending_audio(session)

    assert received == [b"pending"]
    assert session.pending_audio == []


def test_tencent_asr_connect_and_append_audio_with_mock_writer() -> None:
    client = TencentAsrClient()
    received: list[bytes] = []

    asyncio.run(client.connect("s1"))
    client.register_mock_writer("s1", received.append)
    asyncio.run(client.append_audio("s1", b"abc"))

    assert received == [b"abc"]
    assert client.is_ready("s1") is True
