"""测试火山方舟 Coding Plan 的 OpenAI 兼容接口。

用法（在项目 backend 目录下）:
    python scripts/test_ark_coding_openai.py

也可通过环境变量覆盖:
    ARK_BASE_URL  ARK_API_KEY  ARK_MODEL
"""

from __future__ import annotations

import os
import sys

from openai import OpenAI

DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/coding/v3"
DEFAULT_API_KEY = os.getenv("ARK_API_KEY", "")
# Coding Plan 常用模型；若未开通会依次尝试
FALLBACK_MODELS = [
    "ark-code-latest",
    "doubao-seed-2.0-code",
    "deepseek-v3.2",
    "glm-4.7",
    "kimi-k2.5",
    "doubao-1.5-lite-32k",
]


def _client() -> OpenAI:
    base_url = os.getenv("ARK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    api_key = os.getenv("ARK_API_KEY", DEFAULT_API_KEY)
    if not api_key:
        print("错误: 请设置环境变量 ARK_API_KEY", file=sys.stderr)
        sys.exit(1)
    return OpenAI(api_key=api_key, base_url=base_url)


def test_models_list(client: OpenAI) -> bool:
    print("\n[1] GET /models (可选)")
    try:
        models = client.models.list()
        ids = [m.id for m in models.data[:10]]
        print(f"    成功，前 {len(ids)} 个模型: {ids}")
        return True
    except Exception as exc:
        print(f"    跳过（部分端点不支持 list）: {exc}")
        return False


def test_chat_completion(client: OpenAI, model: str) -> bool:
    print(f"\n[2] POST /chat/completions  model={model}")
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是一个简洁助手，只回答一句话。"},
                {"role": "user", "content": "用中文说：Coding Plan OpenAI 兼容测试成功。"},
            ],
            max_tokens=64,
            temperature=0.2,
        )
        text = resp.choices[0].message.content or ""
        print(f"    成功 | id={resp.id} | usage={resp.usage}")
        print(f"    回复: {text.strip()}")
        return True
    except Exception as exc:
        print(f"    失败: {exc}")
        return False


def test_chat_stream(client: OpenAI, model: str) -> bool:
    print(f"\n[3] POST /chat/completions (stream)  model={model}")
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "数到 5，每个数字一行。"}],
            max_tokens=64,
            stream=True,
        )
        chunks: list[str] = []
        for event in stream:
            delta = event.choices[0].delta.content or ""
            if delta:
                chunks.append(delta)
                print(delta, end="", flush=True)
        print()
        print(f"    流式成功，共 {len(chunks)} 个 delta")
        return True
    except Exception as exc:
        print(f"    失败: {exc}")
        return False


def resolve_working_model(client: OpenAI, preferred: str | None) -> str | None:
    candidates = [preferred] if preferred else []
    for name in FALLBACK_MODELS:
        if name and name not in candidates:
            candidates.append(name)

    for model in candidates:
        if test_chat_completion(client, model):
            return model
    return None


def main() -> None:
    base_url = os.getenv("ARK_BASE_URL", DEFAULT_BASE_URL)
    preferred = os.getenv("ARK_MODEL")
    print("=== 火山 Coding Plan · OpenAI 兼容测试 ===")
    print(f"base_url: {base_url}")
    print(f"preferred_model: {preferred or '(自动尝试 FALLBACK_MODELS)'}")

    client = _client()
    test_models_list(client)

    model = resolve_working_model(client, preferred)
    if not model:
        print("\n结论: 配置不可用 — 未找到可用模型，请检查 API Key、套餐与模型开通状态。")
        sys.exit(2)

    if not test_chat_stream(client, model):
        print("\n结论: 非流式可用，流式失败，请检查 stream 权限或模型支持。")
        sys.exit(3)

    print(f"\n结论: 配置可用 [OK]  推荐模型: {model}")
    print("可将以下写入 .env:")
    print(f"  VOICE_ROBOT_DEEPAGENT_ARK_BASE_URL={base_url}")
    print(f"  VOICE_ROBOT_DEEPAGENT_ARK_API_KEY=<你的 key>")
    print(f"  VOICE_ROBOT_DEEPAGENT_ARK_MODEL={model}")


if __name__ == "__main__":
    main()
