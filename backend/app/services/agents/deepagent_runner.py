import logging
import os
from collections.abc import Iterator
from pathlib import Path

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.message import REMOVE_ALL_MESSAGES, RemoveMessage
from langgraph.store.memory import InMemoryStore

from app.core.request_context import get_voice_context
from app.core.settings import Settings
from app.services.agents.wiki_query_tool import build_query_kefu_wiki_tool

try:
    import deepagents as _deepagents_pkg
    from deepagents import create_deep_agent
    from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover
    _deepagents_pkg = None  # type: ignore[assignment]
    create_deep_agent = None  # type: ignore[assignment]
    ChatOpenAI = None  # type: ignore[assignment]
    CompositeBackend = None  # type: ignore[assignment]
    FilesystemBackend = None  # type: ignore[assignment]
    StateBackend = None  # type: ignore[assignment]


class DeepAgentRunner:
    def __init__(self, settings: Settings | None = None) -> None:
        self._logger = logging.getLogger(__name__)
        self._settings = settings or Settings()
        self._agent_name = "deepagent_langchain"
        self._agent = None
        self._greeting_seeded_threads: set[str] = set()

    def _ensure_agent(self):
        if self._agent is None:
            self._agent = self._build_agent()
        return self._agent

    def _build_agent(self):
        if not self._settings.deepagent_enabled:
            self._logger.warning("deepagent disabled (VOICE_ROBOT_DEEPAGENT_ENABLED=false), fallback to mock responses")
            return None
        if create_deep_agent is None or ChatOpenAI is None:
            self._logger.warning("deepagent dependencies missing, fallback to mock responses")
            return None
        if not self._settings.deepagent_ark_api_key.get_secret_value():
            self._logger.warning("missing Ark API key, fallback to mock responses")
            return None

        # 获取项目根目录（当前文件在 backend/app/services/agents/ 下）
        current_file_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.abspath(os.path.join(current_file_dir, "..", "..", ".."))

        # 设置 kefu-knowledge 目录的绝对路径
        kefu_knowledge_dir = os.path.join(project_root, "kefu-know")
        wiki_dir = os.path.join(kefu_knowledge_dir, "wiki")
        os.makedirs(kefu_knowledge_dir, exist_ok=True)

        # skills 目录：deepagents 通过 backend 虚拟路径扫描，不能直接传磁盘绝对路径
        skills_dir = os.path.join(project_root, "skills")
        # 火山 Ark 使用 OpenAI 兼容协议，base_url 对应 /api/v3。
        model = ChatOpenAI(
            model=self._settings.deepagent_ark_model,
            api_key=self._settings.deepagent_ark_api_key.get_secret_value(),
            base_url=self._settings.deepagent_ark_base_url,
            temperature=self._settings.deepagent_temperature,
            timeout=self._settings.deepagent_timeout_seconds,
            streaming=True,
        )

        routes = {
            "/kefu-know/": FilesystemBackend(
                root_dir=kefu_knowledge_dir,
                virtual_mode=True,
            ),
            "/skills/": FilesystemBackend(
                root_dir=skills_dir,
                virtual_mode=True,
            ),
        }

        def _composite_backend(runtime=None):
            default = StateBackend(runtime) if runtime is not None else StateBackend()
            return CompositeBackend(default=default, routes=routes)

        # 0.6.1：StateBackend(runtime=None) 可选，必须传 backend 实例，不能传无参工厂。
        # 若仍报 "takes 0 positional arguments but 1 was given"，说明进程里还是旧的 def _build_backend() 代码，需重启后端。
        version = getattr(_deepagents_pkg, "__version__", "0.0.0")
        version_parts = tuple(int(x) for x in version.split(".")[:2] if x.isdigit())
        use_backend_instance = version_parts >= (0, 5)

        if use_backend_instance:
            backend = _composite_backend()
            self._logger.info(
                "deepagents %s: backend=CompositeBackend instance, skills_route=/skills/ -> %s",
                version,
                skills_dir,
            )
        else:

            def _build_backend(runtime):
                return _composite_backend(runtime)

            backend = _build_backend
            self._logger.info("deepagents %s: backend=factory(StateBackend(runtime))", version)

        agent_tools = []
        if os.path.isdir(wiki_dir):
            agent_tools.append(
                build_query_kefu_wiki_tool(
                    Path(wiki_dir),
                    repo_root=Path(kefu_knowledge_dir),
                    settings=self._settings,
                )
            )
            self._logger.info(
                "deepagent wiki query tool enabled: %s (llm_fallback=%s)",
                wiki_dir,
                self._settings.wiki_query_llm_fallback_enabled,
            )

        # checkpointer：按 config.configurable.thread_id 持久化 messages，同 thread 多轮对话会累积历史
        return create_deep_agent(
            model=model,
            tools=agent_tools,
            system_prompt=self._settings.deepagent_system_prompt,
            store=InMemoryStore(),
            checkpointer=InMemorySaver(),
            skills=["/skills/"] if os.path.isdir(skills_dir) else [],
            backend=backend,
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

    def _build_turn_messages(self, user_text: str, thread_id: str) -> list[dict | AIMessage]:
        """首轮将开场白作为 assistant 历史消息一并注入，后续轮次仅发送用户消息。"""
        messages: list[dict | AIMessage] = []
        greeting = self._settings.greeting_text.strip()
        if (
            thread_id
            and self._settings.greeting_enabled
            and greeting
            and thread_id not in self._greeting_seeded_threads
        ):
            messages.append(AIMessage(content=greeting))
            self._greeting_seeded_threads.add(thread_id)
        messages.append({"role": "user", "content": user_text})
        return messages

    def _prepend_missing_greeting(self, thread_id: str, messages: list[BaseMessage]) -> list[BaseMessage]:
        greeting = self._settings.greeting_text.strip()
        if not thread_id or not self._settings.greeting_enabled or not greeting:
            return messages
        if any(
            isinstance(msg, AIMessage) and self._normalize_message_content(msg.content).strip() == greeting
            for msg in messages
        ):
            return messages
        return [AIMessage(content=greeting), *messages]

    def get_thread_messages(self, thread_id: str) -> list[BaseMessage]:
        """读取当前 thread 的 messages 快照；首轮前若尚未持久化，补上开场白。"""
        agent = self._ensure_agent()
        messages: list[BaseMessage] = []
        if agent is not None:
            snapshot = agent.get_state({"configurable": {"thread_id": thread_id}})
            values = getattr(snapshot, "values", None) or {}
            raw_messages = values.get("messages") or []
            messages = [msg for msg in raw_messages if isinstance(msg, BaseMessage)]
        return self._prepend_missing_greeting(thread_id, messages)

    def replace_thread_messages(self, thread_id: str, messages: list[BaseMessage]) -> None:
        """用完整 messages 覆盖当前 thread 历史。"""
        agent = self._ensure_agent()
        if agent is None:
            return
        agent.update_state(
            {"configurable": {"thread_id": thread_id}},
            {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *messages]},
            as_node="model",
        )

    @staticmethod
    def close_stream(stream_obj: Iterator[str]) -> None:
        close = getattr(stream_obj, "close", None)
        if callable(close):
            close()

    def stream_assistant_text(self, user_text: str, *, thread_id: str) -> Iterator[str]:
        """流式产出助手回复文本增量（字符级），供编排层按标点缓冲后送 TTS。

        thread_id 须在同一会话内保持不变（编排层传 session_id），配合 InMemorySaver 才能带上文。
        每次调用只传入本轮 user 消息，LangGraph 会追加到该 thread 的 checkpoint。
        """
        if self._settings.mock_streaming_enabled:
            yield from self._mock_stream_chars()
            return

        try:
            if self._agent is None:
                self._agent = self._build_agent()
            if self._agent is None:
                self._logger.warning("deepagent agent unavailable, fallback to mock responses")
                yield from self._mock_stream_chars()
                return

            # deepagents 0.6.1 + stream v2：{'type': 'messages', 'data': (AIMessageChunk, meta)}
            self._logger.info("deepagent stream start | thread_id=%s | user_text=%s", thread_id, user_text)
            metadata: dict[str, str] = {}
            voice_ctx = get_voice_context()
            if voice_ctx is not None:
                metadata = {
                    "session_id": voice_ctx.session_id,
                    "turn_id": voice_ctx.turn_id,
                    "trace_id": voice_ctx.trace_id,
                    "input_mode": voice_ctx.input_mode,
                }
            invoke_config = {
                "configurable": {"thread_id": thread_id},
                "metadata": metadata,
            }
            input_messages = self._build_turn_messages(user_text, thread_id)
            for event in self._agent.stream(
                {"messages": input_messages},
                config=invoke_config,
                stream_mode="messages",
                version="v2",
            ):
                if not isinstance(event, dict):
                    continue
                if event.get("type") != "messages":
                    continue
                data = event.get("data")
                if not isinstance(data, (list, tuple)) or len(data) < 1:
                    continue
                message_chunk = data[0]
                if not isinstance(message_chunk, (AIMessage, AIMessageChunk)):
                    continue
                delta = self._normalize_message_content(message_chunk.content)
                if not delta:
                    continue

                self._logger.debug("agent.stream delta | thread_id=%s | len=%s", thread_id, len(delta))
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
