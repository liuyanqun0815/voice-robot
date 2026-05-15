from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    # DeepAgent model (Volcano Ark OpenAI-compatible endpoint)
    deepagent_enabled: bool = True
    deepagent_system_prompt: str = "你是一个简洁、专业的语音助手。"
    deepagent_ark_base_url: str = "https://ark.cn-beijing.volces.com/api/coding/v3"
    deepagent_ark_api_key: SecretStr = SecretStr("")
    deepagent_ark_model: str = "doubao-1.5-lite-32k"
    deepagent_timeout_seconds: int = 30
    deepagent_temperature: float = 0.2
    # Local fallback for dev/testing
    mock_streaming_enabled: bool = False

    model_config = SettingsConfigDict(
        env_prefix="VOICE_ROBOT_",
        env_file=_BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def validate_live_credentials(self) -> None:
        if self.mock_streaming_enabled:
            return

        missing_items: list[str] = []
        if not self.tencent_asr_app_id:
            missing_items.append("VOICE_ROBOT_TENCENT_ASR_APP_ID")
        if not self.tencent_asr_secret_id.get_secret_value():
            missing_items.append("VOICE_ROBOT_TENCENT_ASR_SECRET_ID")
        if not self.tencent_asr_secret_key.get_secret_value():
            missing_items.append("VOICE_ROBOT_TENCENT_ASR_SECRET_KEY")
        if not self.volcano_tts_app_id:
            missing_items.append("VOICE_ROBOT_VOLCANO_TTS_APP_ID")
        if not self.volcano_tts_access_token.get_secret_value():
            missing_items.append("VOICE_ROBOT_VOLCANO_TTS_ACCESS_TOKEN")
        if missing_items:
            missing_text = ", ".join(missing_items)
            raise ValueError(f"Live mode requires credentials: {missing_text}")
