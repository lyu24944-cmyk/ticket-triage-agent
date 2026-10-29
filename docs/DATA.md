# 客户正文：保留期限、加密、DSAR

工单主题和正文会进 `.data/tickets.db`，分类 / 调度 / 起草时也会进模型。  
**SERVICE** 用机器密钥；**REVIEW** 用人员短时 JWT（`sub`），二者继续分离。见 [P1_IDENTITY.md](P1_IDENTITY.md)。  
集成 P3：行上带 `tenant_id`（默认 `default`），主键 `(tenant_id, ticket_id)`；见 [P3_INTEGRATION.md](P3_INTEGRATION.md)。

## 保留多久（可配置、可证明）

| 配置 | 默认 | 含义 |
|------|------|------|
| `TICKET_RETENTION_DAYS` | `90` | 按 `tickets.updated_at`（UTC）。入库和每次改状态会刷新 |

启动 API 时会跑一次过期清理。人员令牌可查：`GET /privacy/retention` → `ticket_retention_days`、`encryption_at_rest`、`purge_on_startup`。

## 过期 / DSAR 删除后清哪里

对目标 `ticket_id`：

1. **`tickets.db`**：整行删除（`subject`、`body`、`history_json`）
2. **`runtime.db`**：同 `ticket_id` 的 `pending_runs`、`closed_runs`、`run_jobs`（含 `request_json` / 检查点）及关联 `trace_spans`
3. **审计**：`privacy.erase` 留下 **正文 SHA-256 指纹**（不含正文），可证明删过

接口：

- `GET /privacy/tickets/{ticket_id}` — 导出（DSAR）
- `DELETE /privacy/tickets/{ticket_id}` — 硬删 + 证明

不删：知识库、评测集、日志文件旧行（日志不应含正文明文）。

## 静态加密与传输

| 层 | 做法 |
|----|------|
| 传输 | 网关 TLS 终结（[GATEWAY.md](GATEWAY.md)） |
| 静态 | 生产优先磁盘/卷加密；可选 `DATA_AT_REST_KEY` 对 subject/body Fernet（库内 `enc:v1:…`） |

## 哪些字段可以进提示词

| 可以 | 不可以 |
|------|--------|
| `ticket_id` | `customer_email` |
| 脱敏后的 `subject`、`body` | `metadata` |
| 分类结果的类别 / 优先级 | `reviewer`（审核人） |
| 工具结果摘要（检索 query、历史 **status 列表**） | 历史里的 `note`、`reviewer` |
| 本次检索到的知识库文档 | 受理密钥 / 审核 JWT |
| 上一步错误码（如 `enrich_failed`） | 原文里的邮箱、电话、卡号、密钥串 |

送出前由 `ticket_triage.privacy` 替换：`[EMAIL]` `[PHONE]` `[CARD]` `[SECRET]`。

## 审核人是谁

- 身份来自 **JWT `sub`**，不是请求体里的 `reviewer` 字符串（body 可省略，伪造无效）
- 引导密钥 `REVIEW_API_KEY` 只用于 `POST /auth/review/token` 换票，**不能**直接调人审接口
- `SERVICE_API_KEY` 只受理 / 轮询运行，不能人审
