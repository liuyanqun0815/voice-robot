import logging
from collections.abc import Iterator

from langchain_core.messages import AIMessage, AIMessageChunk
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from app.core.settings import Settings

try:
    from deepagents import create_deep_agent
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover
    create_deep_agent = None  # type: ignore[assignment]
    ChatOpenAI = None  # type: ignore[assignment]


class DeepAgentRunner:
    def __init__(self, settings: Settings | None = None) -> None:
        self._logger = logging.getLogger(__name__)
        self._settings = settings or Settings()
        self._agent_name = "deepagent_langchain"
        self._agent = None

    def _build_agent(self):
        if not self._settings.deepagent_enabled:
            return None
        if create_deep_agent is None or ChatOpenAI is None:
            self._logger.warning("deepagent dependencies missing, fallback to mock responses")
            return None
        if not self._settings.deepagent_ark_api_key.get_secret_value():
            self._logger.warning("missing Ark API key, fallback to mock responses")
            return None

        # 火山 Ark 使用 OpenAI 兼容协议，base_url 对应 /api/v3。
        model = ChatOpenAI(
            model=self._settings.deepagent_ark_model,
            api_key=self._settings.deepagent_ark_api_key.get_secret_value(),
            base_url=self._settings.deepagent_ark_base_url,
            temperature=self._settings.deepagent_temperature,
            timeout=self._settings.deepagent_timeout_seconds,
            streaming=True,
        )
        return create_deep_agent(
            model=model,
            tools=[],
            system_prompt=self._settings.deepagent_system_prompt,
            store=InMemoryStore(),
            checkpointer=InMemorySaver(),
        )

    def _mock_stream_chars(self) -> Iterator[str]:
        full = "好的，我正在为你处理。请稍等片刻。"
        yield from full

    @staticmethod
    def _normalize_message_content(content: object) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif isinstance(block, str):
                    parts.append(block)
            return "".join(parts)
        return ""

    def stream_assistant_text(self, user_text: str, *, thread_id: str) -> Iterator[str]:
        """流式产出助手回复文本增量（字符级），供编排层按标点缓冲后送 TTS。"""
        if self._settings.mock_streaming_enabled:
            yield from self._mock_stream_chars()
            return

        try:
            if self._agent is None:
                self._agent = self._build_agent()
            if self._agent is None:
                yield from self._mock_stream_chars()
                return

            # messages：按 LLM token 流式产出 (message_chunk, metadata)。
            # values：每步输出整图状态，需对 AIMessage.content 做 diff，粒度粗且延迟高。
            # version="v2" 需 LangGraph>=1.1；当前 1.0.10 使用 (chunk, metadata) 二元组格式。
            for chunk in self._agent.stream(
                {"messages": [{"role": "user", "content": user_text}]},
                config={"configurable": {"thread_id": thread_id}},
                stream_mode="messages",
                    version="v2",
            ):
                if not isinstance(chunk, tuple) or len(chunk) != 2:
                    continue
                message_chunk, _metadata = chunk
                if not isinstance(message_chunk, (AIMessage, AIMessageChunk)):
                    continue
                delta = self._normalize_message_content(message_chunk.content)
                if not delta:
                    continue
                # self._logger.info("deepagent stream delta: %r", delta)
                yield delta
        except Exception as exc:
            hint = self._format_stream_error(exc)
            self._logger.warning("deepagent stream failed, fallback to mock: %s", hint)
            yield from self._mock_stream_chars()

    def _format_stream_error(self, exc: Exception) -> str:
        text = str(exc)
        if "ModelNotOpen" in text or "has not activated the model" in text:
            return (
                f"Ark 模型未开通: {self._settings.deepagent_ark_model}，"
                "请在火山方舟控制台开通该模型，或将 VOICE_ROBOT_DEEPAGENT_ARK_MODEL 改为已开通的端点"
                f"（如 doubao-1.5-lite-32k）。原始错误: {text}"
            )
        return text

    def run_sentences(self, user_text: str) -> list[str]:
        """非流式：整段拉取后按句号粗切（脚本/兼容用）。"""
        full = "".join(self.stream_assistant_text(user_text, thread_id="offline"))
        if not full.strip():
            return ["收到你的问题。"]
        return [s.strip() for s in full.split("。") if s.strip()]
