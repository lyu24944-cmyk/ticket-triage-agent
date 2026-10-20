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
    # P2：是否通过 MCP 调用工具
    use_mcp: bool = False
    # MCP 工具调用超时（秒）
    mcp_timeout_seconds: float = 5.0
    # 启动 MCP 子进程的命令；空则用当前解释器 -m mcp_server
    mcp_command: str | None = None
    # 工单 SQLite 路径（Agent 与 MCP 共享）；空则用项目 .data/tickets.db
    ticket_db_path: str | None = None


settings = Settings()
