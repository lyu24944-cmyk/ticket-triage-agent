# P3 集成与产品化

与「评测门禁 P3」（`docs/P2_MCP_P3_EVALS.md`）区分：本页是 **Webhook 进线、/v1 API、轻量多租户、结案回调**。

## 验收路径

```text
模拟 Zendesk/邮件网关
  → POST /v1/webhooks/zendesk|email（HMAC 签名 + Delivery-Id 幂等）
  → 待审 GET /v1/runs/pending
  → POST /v1/runs/human 通过
  → 出站 POST callback_url（ticket.closed，HMAC 签名 + Delivery-Id；失败有限重试，不回滚结案）
重复同一 Delivery-Id → 回放同一 trace，不双单
```

本地一键：

```powershell
.\.venv\Scripts\python.exe scripts/demo_p3_integration.py
pytest tests/test_p3_integration.py -q
```

## 对外 API（`/v1`）

旧路径（`/runs`、`/tickets`…）仍可用，内部委托同一套逻辑。**审核台（Vite+React）只打 `/v1`**，含分页 `cursor` / `limit`。

| 能力 | 说明 |
|------|------|
| 错误信封 | `{"error":{"code","message"},"trace_id"}`；机读表 `GET /v1/errors` |
| Idempotency-Key | `POST /v1/runs`；同键同体回放，同键异体 `409 idempotency_conflict` |
| 分页检索 | `GET /v1/tickets`、`GET /v1/runs/pending`：`q` / `cursor` / `limit` → `{items,next_cursor}` |
| 审核台 | `GET /review` 托管 `web/` 构建产物（`api/static/review`）。先 `scripts/build_review.ps1` 或 `cd web && npm run build` |

## Webhook

| 头 | Zendesk | 邮件网关 |
|----|---------|----------|
| 签名 | `X-Zendesk-Webhook-Signature: sha256=<hex>` | `X-Email-Gateway-Signature` |
| 幂等 | `X-Zendesk-Delivery-Id` | `X-Email-Gateway-Delivery-Id` 或 `message_id` |

HMAC-SHA256(secret, raw_body)。密钥：单租户 `WEBHOOK_SIGNING_SECRET`，或多租户 `TENANTS_JSON[].webhook_secret`。

## 多租户（轻量）

- 默认 `tenant_id=default`；单库加列，复合主键 `(tenant_id, ticket_id)`。
- `X-API-Key` → 租户；换票 JWT 写入 `tid`。
- 每租户 `queue_limit`（缺省 `RUN_QUEUE_LIMIT`）。
- 结案回调：`CLOSURE_CALLBACK_URL` 或租户 `callback_url`。
  - 头：`X-Ticket-Triage-Signature: sha256=<HMAC-SHA256(secret, body)>`（密钥同进线 `WEBHOOK_SIGNING_SECRET` / 租户 `webhook_secret`）；`X-Ticket-Triage-Delivery-Id`。
  - 失败写入 runtime `closure_callbacks`，指数退避重试（`CLOSURE_CALLBACK_MAX_ATTEMPTS` 默认 4）；耗尽标 `dead` 并告警 `closure_callback_failed`，**不回滚**已写工单状态。

示例：

```json
[
  {
    "tenant_id": "acme",
    "service_api_key": "s1",
    "review_api_key": "r1",
    "webhook_secret": "wh1",
    "callback_url": "http://127.0.0.1:9/cb",
    "queue_limit": 8
  }
]
```

## 非目标

真实 Zendesk OAuth、每租户独立库、多副本写、审核 JWT 强制 SSO。
