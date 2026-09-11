"""Configuration settings for Gemini Telegram Bot."""

from pathlib import Path
from typing import Optional, Set
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Telegram
    bot_token: str = Field(default="", validation_alias="BOT_TOKEN")
    allowed_telegram_ids: Set[int] = Field(default_factory=set, validation_alias="ALLOWED_TELEGRAM_IDS")
    telegram_proxy_url: Optional[str] = Field(default=None, validation_alias="TELEGRAM_PROXY_URL")

    # LLM (Antigravity / OpenAI-compatible Gemini API)
    openai_base_url: str = Field(default="http://127.0.0.1:8400/v1", validation_alias="OPENAI_BASE_URL")
    openai_api_key: str = Field(default="sk-antigravity", validation_alias="OPENAI_API_KEY")
    default_model: str = Field(default="agy/gemini-3.7-flash-high", validation_alias="DEFAULT_MODEL")
    router_model: str = Field(default="agy/gemini-2.5-flash", validation_alias="ROUTER_MODEL")

    # Web Search & Internet Intelligence
    web_search_enabled: bool = Field(default=True, validation_alias="WEB_SEARCH_ENABLED")
    max_search_results: int = Field(default=5, validation_alias="MAX_SEARCH_RESULTS")
    web_fetch_timeout: float = Field(default=8.0, validation_alias="WEB_FETCH_TIMEOUT")
    default_web_mode: str = Field(default="auto", validation_alias="DEFAULT_WEB_MODE")  # 'auto', 'on', 'off'

    # Context & Compression
    max_context_tokens: int = Field(default=32000, validation_alias="MAX_CONTEXT_TOKENS")
    protect_last_n_messages: int = Field(default=6, validation_alias="PROTECT_LAST_N_MESSAGES")
    system_prompt: str = Field(
        default=(
            "You are an intelligent, helpful, and concise AI assistant powered by Gemini. "
            "You format answers nicely using Telegram Markdown, explain complex concepts clearly, "
            "and adapt to the user's language and tone."
        ),
        validation_alias="SYSTEM_PROMPT"
    )

    # UI / Streaming
    stream_update_interval: float = Field(default=0.8, validation_alias="STREAM_UPDATE_INTERVAL")

    # Database
    db_path: str = Field(default="data/bot.db", validation_alias="DB_PATH")

    @field_validator("allowed_telegram_ids", mode="before")
    @classmethod
    def parse_allowed_ids(cls, v):
        if isinstance(v, int):
            return {v}
        if isinstance(v, str):
            if not v.strip():
                return set()
            return {int(x.strip()) for x in v.split(",") if x.strip().isdigit()}
        if isinstance(v, (list, set, tuple)):
            return {int(x) for x in v}
        return set()

    def get_db_path(self) -> Path:
        p = Path(self.db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


config = Settings()
