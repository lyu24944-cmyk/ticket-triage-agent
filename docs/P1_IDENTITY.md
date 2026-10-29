# P1：身份、审计与数据合规

## 身份

| 角色 | 凭证 | 用途 |
|------|------|------|
| 机器 SERVICE | `X-API-Key: SERVICE_API_KEY` | `POST /runs`、`GET /runs/{trace_id}` |
| 引导（换票） | `X-API-Key: REVIEW_API_KEY` | 仅本地 mint：`POST /auth/review/token` |
| 人员 REVIEW | `Authorization: Bearer <JWT>` | 待审、人审、导出/删除、审计查询 |

JWT：`sub` = 审核人；租户取 `tid` / `tenant_id`（OIDC 可用 `REVIEW_OIDC_TENANT_CLAIM`）。

| 模式 | 何时 | 签发 | 验签 |
|------|------|------|------|
| mint（默认） | 未配 `REVIEW_OIDC_ISSUER` | `POST /auth/review/token`（HS256） | 本地 `REVIEW_JWT_SECRET` |
| OIDC（内测） | 设 `REVIEW_OIDC_ISSUER` | **mint 关闭**（`review_mint_disabled`） | IdP JWKS / RS256；`iss`/`aud`/`exp`/`sub` |

`GET /auth/review/mode` 返回当前模式。验收：OIDC 开启后无 IdP 令牌不能人审；验签与 `sub` 口径不变。

## 审计

`runtime.db` 表 `audit_events`：`actor` / `action` / `ticket_id` / `trace_id` / `at`。  
动作含 `review.read_drafts`、`review.approve`、`review.reject`、`review.reopen`、`privacy.export`、`privacy.erase`。  
查询：`GET /audit/events?ticket_id=`。

## 红队

`evals/golden/redteam.jsonl` + `python evals/run_redteam.py`；CI `evals.yml` 失败挡发布。覆盖工具白名单与跨工单 history。

## 验收

1. 无有效人员 JWT → 人审接口 401 `review_token_missing`  
2. 结案后 `GET /audit/events?ticket_id=` 能追到 `review.approve` + `trace_id`  
3. `DELETE /privacy/tickets/{id}` 后 tickets/runtime 无正文残留；审计有指纹证明  
