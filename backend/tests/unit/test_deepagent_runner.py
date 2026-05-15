import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.core.settings import Settings
from app.services.agents.deepagent_runner import DeepAgentRunner

_MOCK_TEXT = "好的，我正在为你处理"


def test_deepagent_runner_returns_sentences_mock() -> None:
    """离线 mock：不访问 Ark。"""
    runner = DeepAgentRunner(settings=Settings())

    output = runner.run_sentences("帮我查询订单状态")

    assert isinstance(output, list)
    assert len(output) >= 1
    assert any(_MOCK_TEXT in sentence for sentence in output)


@pytest.mark.live
def test_deepagent_runner_live_ark(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """真实调用火山 Ark（需 .env 中有效 API Key 与已开通的模型/接入点）。"""
    settings = Settings()
    if not settings.deepagent_ark_api_key.get_secret_value():
        pytest.skip("未配置 VOICE_ROBOT_DEEPAGENT_ARK_API_KEY")
    if settings.mock_streaming_enabled:
        pytest.skip("请设置 VOICE_ROBOT_TEST_LIVE=1 后再跑 -m live")
    if not settings.deepagent_enabled:
        pytest.skip("live 模式下需开启 VOICE_ROBOT_DEEPAGENT_ENABLED")

    runner = DeepAgentRunner(settings=settings)
    prompt = "用一句话介绍你自己"
    chunks: list[str] = []
    with caplog.at_level("WARNING"):
        with capsys.disabled():
            print("\n--- live ark 流式返回 ---")
            for delta in runner.stream_assistant_text(prompt, thread_id="pytest-live"):
                print(delta, end="", flush=True)
                chunks.append(delta)
            print()
        full_text = "".join(chunks)

    with capsys.disabled():
        print("\n--- live ark 返回值 (full_text) ---")
        print(full_text)
        print("--- live ark run_sentences 等价结果 ---")
        print([s.strip() for s in full_text.split("。") if s.strip()])
        print(f"--- model={settings.deepagent_ark_model} ---\n")

    if _MOCK_TEXT in full_text:
        ark_errors = [r.getMessage() for r in caplog.records if "deepagent stream failed" in r.getMessage()]
        detail = ark_errors[0] if ark_errors else "未知错误"
        pytest.fail(
            f"Ark 未成功（已 fallback mock）。请检查 VOICE_ROBOT_DEEPAGENT_ARK_MODEL="
            f"{settings.deepagent_ark_model} 是否为控制台已开通的接入点 ID。详情: {detail}"
        )

    assert full_text.strip(), "Ark 应返回非空文本"
