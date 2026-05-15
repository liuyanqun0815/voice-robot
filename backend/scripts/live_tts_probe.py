import asyncio
import json
import os
import time

import websockets


async def main() -> None:
    uri = os.getenv("VOICE_ROBOT_PROBE_WS_URL", "ws://127.0.0.1:8000/ws/voice")
    suffix = str(int(time.time() * 1000))
    session_id = f"tts_s_{suffix}"
    turn_id = f"tts_t_{suffix}"
    async with websockets.connect(uri) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "turn_commit_request",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "seq": 1,
                    "trace_id": "tr_tts_probe",
                    "timestamp_ms": 1,
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
