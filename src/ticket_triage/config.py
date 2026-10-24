from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """运行时配置（可用环境变量 / .env 覆盖）。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "ticket-triage-agent"
    # stub：无需 API Key；anthropic / openai 有 Key 时用真实模型分类和起草
    llm_provider: str = "stub"
    llm_model: str | None = None
    llm_timeout_seconds: float = 30.0
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_base_url: str = "https://cloud.langfuse.com"
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
    # P5：暂停状态与节点追踪
    runtime_db_path: str | None = None
    trace_budget_ms: float = 2000.0
    # P6：设置后，知识库检索改调 citation-rag-agent，例如 http://127.0.0.1:8001。
    # local 表示仍用仓库里的关键词文档。空字符串不会覆盖 .env 里已有的地址。
    rag_base_url: str | None = None
    rag_timeout_seconds: float = 10.0

    @field_validator("rag_base_url", mode="before")
    @classmethod
    def local_rag_uses_keyword_kb(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and value.strip().lower() in {"", "local", "off", "none"}:
            return None
        return value


settings = Settings()
