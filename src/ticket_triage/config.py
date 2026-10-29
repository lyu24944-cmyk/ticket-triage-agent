from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """运行时配置（可用环境变量 / .env 覆盖）。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "ticket-triage-agent"
    # stub：无需 API Key；anthropic / openai 有 Key 时用真实模型分类和起草
    llm_provider: str = "stub"
    llm_model: str | None = None
    llm_timeout_seconds: float = 30.0
    # 分类、调度、起草共用的上下文上限。超出后先删最早的工具结果。
    context_token_budget: int = 1200
    # 提示词销钉：current=v3，previous=v2。CI 只跑当前销钉，不在一次 CI 里打两次外网模型。
    prompt_pin: str = "current"
    openai_api_key: str | None = Field(default=None, repr=False)
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str | None = Field(default=None, repr=False)
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = Field(default=None, repr=False)
    langfuse_base_url: str = "https://cloud.langfuse.com"
    # 人审开关只看这里。请求体不能把它关掉。
    require_hitl: bool = True
    # 受理和查询运行结果的服务身份。空则 POST /runs 与 GET /runs/{trace_id} 一律拒绝。
    # 生产用 *_FILE 挂 Secret Manager / K8s secret；见 docs/SECRETS.md。
    service_api_key: str | None = Field(default=None, repr=False)
    service_api_key_file: str | None = None
    # 轮换窗口：旧密钥仍可接受，直到摘掉 PREVIOUS。
    service_api_key_previous: str | None = Field(default=None, repr=False)
    service_api_key_previous_file: str | None = None
    # 审核接口密钥。空则待审列表、人审和重新送审一律拒绝。
    review_api_key: str | None = Field(default=None, repr=False)
    review_api_key_file: str | None = None
    review_api_key_previous: str | None = Field(default=None, repr=False)
    review_api_key_previous_file: str | None = None
    max_tool_retries: int = 2
    # 这个进程里同时执行几张工单。任务记在 SQLite，不是线程池里的闭包。
    run_workers: int = 2
    # 已受理但还没结束的单（含正在执行）超过这个数，新工单直接拒绝。
    run_queue_limit: int = 32
    # P2：是否通过 MCP 调用工具
    use_mcp: bool = False
    # MCP 工具调用超时（秒）
    mcp_timeout_seconds: float = 5.0
    # 启动 MCP 子进程的命令；空则用当前解释器 -m mcp_server
    mcp_command: str | None = None
    # 工单 SQLite 路径（Agent 与 MCP 共享）；空则用项目 .data/tickets.db
    ticket_db_path: str | None = None
    # 工单正文在 SQLite 的保留天数。过期后删 tickets 行，以及同 ticket_id 的 pending/closed/run_jobs/spans。
    ticket_retention_days: int = 90
    # 审核人员 JWT：签发密钥（空则从 REVIEW_API_KEY 派生）。TTL 默认 1 小时。
    # 配置 REVIEW_OIDC_ISSUER 后改验 IdP 令牌，mint 关闭；验签与 sub 口径不变。
    review_jwt_secret: str | None = Field(default=None, repr=False)
    review_jwt_ttl_seconds: int = 3600
    review_oidc_issuer: str | None = None
    review_oidc_audience: str = "ticket-triage-review"
    review_oidc_jwks_url: str | None = None
    review_oidc_tenant_claim: str = "tid"
    review_oidc_jwks_cache_seconds: int = 300
    review_oidc_http_timeout_seconds: float = 5.0
    # 静态加密口令（任意字符串，派生 Fernet key）。空则明文 + 须磁盘加密。
    data_at_rest_key: str | None = Field(default=None, repr=False)
    # P5：暂停状态与追踪库；空则用项目 .data/runtime.db
    runtime_db_path: str | None = None
    trace_budget_ms: float = 2000.0
    # P6：设置后，知识库检索改调 citation-rag-agent，例如 http://127.0.0.1:8001。
    # local 表示仍用仓库里的关键词文档。空字符串不会覆盖 .env 里已有的地址。
    rag_base_url: str | None = None
    rag_timeout_seconds: float = 10.0
    # P0.5：ops 日志打成 JSON 一行，方便采集进集中日志（仍不上完整 APM）。
    ops_log_json: bool = False
    # 告警：Webhook（飞书/钉钉/Slack 等入站）或 SMTP。空则只打日志不外发。
    alert_webhook_url: str | None = None
    alert_webhook_timeout_seconds: float = 5.0
    alert_cooldown_seconds: int = 60
    alert_email_to: str | None = None
    alert_email_from: str | None = None
    alert_smtp_host: str | None = None
    alert_smtp_port: int = 587
    alert_smtp_user: str | None = None
    alert_smtp_password: str | None = Field(default=None, repr=False)
    alert_smtp_use_tls: bool = True
    # P3 集成：多租户 JSON（空则单租户 default，回退 SERVICE/REVIEW 密钥）。
    tenants_json: str | None = Field(default=None, repr=False)
    # 单租户默认 Webhook HMAC 密钥与结案回调。
    webhook_signing_secret: str | None = Field(default=None, repr=False)
    closure_callback_url: str | None = None
    # Idempotency-Key 保留秒数。
    idempotency_ttl_seconds: int = 86400
    # 出站结案回调超时。
    closure_callback_timeout_seconds: float = 5.0

    @field_validator("prompt_pin", mode="before")
    @classmethod
    def normalize_prompt_pin(cls, value: object) -> object:
        if value is None:
            return "current"
        if isinstance(value, str) and not value.strip():
            return "current"
        return value

    @field_validator(
        "review_api_key",
        "service_api_key",
        "review_api_key_previous",
        "service_api_key_previous",
        "review_api_key_file",
        "service_api_key_file",
        "review_api_key_previous_file",
        "service_api_key_previous_file",
        "openai_api_key",
        "anthropic_api_key",
        "langfuse_secret_key",
        "alert_smtp_password",
        "alert_webhook_url",
        "alert_email_to",
        "alert_email_from",
        "alert_smtp_host",
        "alert_smtp_user",
        "review_jwt_secret",
        "review_oidc_issuer",
        "review_oidc_jwks_url",
        "data_at_rest_key",
        "tenants_json",
        "webhook_signing_secret",
        "closure_callback_url",
        mode="before",
    )
    @classmethod
    def blank_secret_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("rag_base_url", mode="before")
    @classmethod
    def local_rag_uses_keyword_kb(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and value.strip().lower() in {"", "local", "off", "none"}:
            return None
        return value


settings = Settings()
