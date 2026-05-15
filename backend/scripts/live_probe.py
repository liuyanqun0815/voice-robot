import asyncio
import base64
import json
import os
import time

import websockets


async def main() -> None:
    uri = os.getenv("VOICE_ROBOT_PROBE_WS_URL", "ws://127.0.0.1:8000/ws/voice")
    suffix = str(int(time.time() * 1000))
    session_id = f"live_s_{suffix}"
    turn_id = f"live_t_{suffix}"
    async with websockets.connect(uri) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "audio_chunk",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "seq": 1,
                    "trace_id": "tr_live",
                    "timestamp_ms": 1,
                    "audio_base64": base64.b64encode(b"\x00" * 3200).decode("ascii"),
                }
            )
        )
        await ws.send(
            json.dumps(
                {
                    "type": "turn_commit_request",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "seq": 2,
                    "trace_id": "tr_live",
                    "timestamp_ms": 2,
                    "reason": "请读一句测试文本",
                }
            )
        )

        for _ in range(12):
            try:
                message = await asyncio.wait_for(ws.recv(), timeout=5)
            except TimeoutError:
                print('{"type":"probe_timeout"}')
                break
            print(message)
            event_type = json.loads(message).get("type", "")
            if event_type in {"audio_complete", "error"}:
                break


if __name__ == "__main__":
    asyncio.run(main())
