import os

# 默认单测走 mock，保证 CI/本地离线可重复。
# 真实 Ark 调用：设置 VOICE_ROBOT_TEST_LIVE=1 后执行 pytest -m live
_LIVE = os.environ.get("VOICE_ROBOT_TEST_LIVE", "").lower() in ("1", "true", "yes")

if _LIVE:
    os.environ["VOICE_ROBOT_MOCK_STREAMING_ENABLED"] = "false"
    os.environ["VOICE_ROBOT_DEEPAGENT_ENABLED"] = "true"
else:
    os.environ["VOICE_ROBOT_MOCK_STREAMING_ENABLED"] = "true"
os.environ.setdefault("VOICE_ROBOT_AUDIT_ENABLED", "false")
