from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """运行时配置（可用环境变量 / .env 覆盖）。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "ticket-triage-agent"
    # stub：无需 API Key；openai / anthropic 留给后续接入
    llm_provider: str = "stub"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    require_hitl: bool = True
    max_tool_retries: int = 2


settings = Settings()
