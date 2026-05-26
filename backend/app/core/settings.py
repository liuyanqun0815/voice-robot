import os
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_GREETING_TEXT = (
    "Hi，我是SCNet专属支持工程师，很高兴为您服务。\n"
    "如果您有意向参与核心节点邀测计划，前往报名体验 。如需了解更多详情，点击下方【核心节点】专属入口咨询客服。\n"
    "如果您有其他问题需要咨询，请在下方详细描述，我们将及时为您解答🤝"
)

_DEFAULT_DEEPAGENT_SYSTEM_PROMPT = (
    "你的身份是超算互联网平台的客服经理。\n"
    "\n"
    "## 身份与语气\n"
    "- 语气亲切、有耐心、专业。\n"
    "- 过渡语自然，尽可能称客户为老师。\n"
    "- 可以说：老师您好，我先帮您看一下这个情况。\n"
    "\n"
    "## 回答原则\n"
    "- 回答产品政策、计费试用、API、作业、平台使用等问题时，必须先调用 query_kefu_wiki 获取内部依据。\n"
    "- 仅根据工具返回内容作答，不要编造。\n"
    "- 先结论，后简短步骤，必要时给出下一步建议。\n"
    "- 输出 token 控制在 100 个以内。\n"
    "\n"
    "## 禁止事项\n"
    "- 禁止出现“知识库”“查询知识库”“未收录”“内部资料显示”等表述。\n"
    "- 不要暴露内部检索过程。\n"
    "\n"
    "## 依据不足时\n"
    "- 统一引导客户补充信息。\n"
    "- 示例：老师您好，麻烦您再补充一下具体报错、操作步骤或截图，我来继续帮您看。"
)

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    app_env: str = "development"
    app_port: int = 8000
    # Tencent realtime ASR
    tencent_asr_app_id: str = ""
    tencent_asr_secret_id: SecretStr = SecretStr("")
    tencent_asr_secret_key: SecretStr = SecretStr("")
    tencent_asr_engine_model_type: str = "16k_zh_large"
    tencent_asr_need_vad: int = 1
    tencent_asr_vad_silence_time_ms: int = 500
    tencent_asr_noise_threshold: float = 0.8
    # Volcano TTS v3 双向流式（与官方 bidirection demo 一致）
    volcano_tts_ws_url: str = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
    volcano_tts_app_id: str = ""
    volcano_tts_access_token: SecretStr = SecretStr("")
    # 为空则按音色自动选择（S_ 前缀 -> megatts，否则 volc.service_type.10029）
    volcano_tts_resource_id: str = ""
    volcano_tts_voice_type: str = "zh_female_qingxinnvsheng"
    volcano_tts_audio_format: str = "mp3"
    volcano_tts_sample_rate: int = 24000
    tts_enabled: bool = True
    # DeepAgent model (Volcano Ark OpenAI-compatible endpoint)
    deepagent_enabled: bool = True
    deepagent_system_prompt: str = Field(default=_DEFAULT_DEEPAGENT_SYSTEM_PROMPT)
    deepagent_ark_base_url: str = "https://ark.cn-beijing.volces.com/api/coding/v3"
    deepagent_ark_api_key: SecretStr = SecretStr("")
    deepagent_ark_model: str = "doubao-1.5-lite-32k"
    deepagent_timeout_seconds: int = 30
    deepagent_temperature: float = 0.2
    # wiki 检索：index 命中不足时用 Ark 从 index 选路径（对齐 llm-wiki query.py，额外 1 次 LLM）
    wiki_query_llm_fallback_enabled: bool = False
    wiki_query_index_llm_max_pages: int = 5
    # 客户消息中的 http 链接：图片 OCR + 网页正文（再送入 DeepAgent）
    link_enrichment_enabled: bool = True
    link_enrichment_vision_model: str = ""
    link_enrichment_timeout_seconds: int = 30
    link_enrichment_max_urls: int = 3
    link_enrichment_max_page_bytes: int = 512_000
    link_enrichment_max_page_chars: int = 8000
    link_enrichment_user_agent: str = "VoiceRobot/1.0 (+link-enrichment)"
    link_enrichment_local_ocr_fallback_enabled: bool = True
    # 审计落库（Phase 1）
    audit_enabled: bool = False
    audit_dsn: str = "sqlite:///./data/audit.db"
    audit_store_user_text: bool = True
    audit_max_user_text_chars: int = 2000
    audit_admin_api_key: SecretStr = SecretStr("")
    # 前端运维页地址（后端旧版 HTML 仪表盘重定向用）
    frontend_ops_url: str = "http://127.0.0.1:5173/#/ops"
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    # WebSocket 建连后主动下发的开场白（不经过 LLM）
    greeting_enabled: bool = True
    greeting_text: str = Field(default=_DEFAULT_GREETING_TEXT)
    greeting_stream_chunk_chars: int = 2
    greeting_stream_interval_ms: int = 40
    # Local fallback for dev/testing
    mock_streaming_enabled: bool = False

    @field_validator("greeting_text", mode="before")
    @classmethod
    def _normalize_greeting_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.replace("\\n", "\n").strip()
        return value

    @field_validator("deepagent_system_prompt", mode="before")
    @classmethod
    def _normalize_deepagent_system_prompt(cls, value: object) -> object:
        if isinstance(value, str):
            return value.replace("\\n", "\n").strip()
        return value

    # LangSmith / LangChain tracing（使用标准环境变量名，不受 VOICE_ROBOT_ 前缀影响）
    langchain_tracing_v2: bool = Field(default=False, validation_alias="LANGCHAIN_TRACING_V2")
    langsmith_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY"),
    )
    langchain_project: str = Field(default="", validation_alias="LANGCHAIN_PROJECT")

    model_config = SettingsConfigDict(
        env_prefix="VOICE_ROBOT_",
        env_file=_BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def apply_langsmith_environment(self) -> None:
        """在首次 import langchain 之前写入 os.environ，以便追踪客户端能正确初始化。"""
        if self.langchain_tracing_v2:
            os.environ["LANGCHAIN_TRACING_V2"] = "true"
        api_key = self.langsmith_api_key.get_secret_value()
        if api_key:
            os.environ["LANGSMITH_API_KEY"] = api_key
            os.environ["LANGCHAIN_API_KEY"] = api_key
        if self.langchain_project:
            os.environ["LANGCHAIN_PROJECT"] = self.langchain_project

    def resolve_audit_dsn(self) -> str:
        dsn = self.audit_dsn.strip()
        if dsn.startswith("sqlite:///./"):
            rel = dsn.removeprefix("sqlite:///./")
            path = (_BACKEND_ROOT / rel).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{path.as_posix()}"
        return dsn

    def readiness_missing_items(self) -> list[str]:
        if self.mock_streaming_enabled:
            return []
        missing_items: list[str] = []
        if not self.tencent_asr_app_id:
            missing_items.append("VOICE_ROBOT_TENCENT_ASR_APP_ID")
        if not self.tencent_asr_secret_id.get_secret_value():
            missing_items.append("VOICE_ROBOT_TENCENT_ASR_SECRET_ID")
        if not self.tencent_asr_secret_key.get_secret_value():
            missing_items.append("VOICE_ROBOT_TENCENT_ASR_SECRET_KEY")
        if self.tts_enabled:
            if not self.volcano_tts_app_id:
                missing_items.append("VOICE_ROBOT_VOLCANO_TTS_APP_ID")
            if not self.volcano_tts_access_token.get_secret_value():
                missing_items.append("VOICE_ROBOT_VOLCANO_TTS_ACCESS_TOKEN")
        if self.deepagent_enabled and not self.deepagent_ark_api_key.get_secret_value():
            missing_items.append("VOICE_ROBOT_DEEPAGENT_ARK_API_KEY")
        return missing_items

    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    def validate_live_credentials(self) -> None:
        missing_items = self.readiness_missing_items()
        if missing_items:
            missing_text = ", ".join(missing_items)
            raise ValueError(f"Live mode requires credentials: {missing_text}")
