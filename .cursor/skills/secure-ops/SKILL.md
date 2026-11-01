---
name: secure-ops
description: 鉴权、单进程锁、密钥与镜像非 root 的硬边界。Use when touching auth, API keys, .env, SQLite data dirs, instance.lock, Docker, Trivy, or deploy. SERVICE 与 REVIEW 不能混用。
---

# 安全与运行边界

这些是已锁定的约束。改代码时遵守，不要顺手放宽。细节以文档为准。不要在回复、日志或提交里打印密钥。

## 两套身份（不要混用请求头）

见 `docs/P1_IDENTITY.md`、`docs/SECRETS.md`。

| 角色 | 请求头 | 只用在 |
|------|--------|--------|
| SERVICE | `X-API-Key: SERVICE_API_KEY` | `POST /runs`、`GET /runs/{trace_id}` |
| 换票 | `X-API-Key: REVIEW_API_KEY` | 仅 `POST /auth/review/token`。设了 `REVIEW_OIDC_ISSUER` 后换票关闭 |
| REVIEW | `Authorization: Bearer <JWT>` | 待审、人审、工单列表、重新送审、导出/删除、审计 |

- `REVIEW_API_KEY` 不能直接调人审。人审没有 Bearer 时返回 `review_token_missing`。
- 受理未配置密钥时返回 `service_auth_not_configured`，不是匿名放行。
- `trace_id` 不是读草稿的凭证。是否跳过人审只看 `REQUIRE_HITL`；请求体里的 `auto_approve` 会被忽略。
- 生产用 `*_FILE` 挂密钥，轮换窗口用 `*_PREVIOUS`。密钥不进镜像、不进日志。

## 不要提交秘密，不要双写 SQLite

- `.env` 与 `.data/` 已在 `.gitignore`。不要 `git add -f`，不要把真实 Key 写进仓库或评测报告。
- 一个数据目录只跑一个 API 进程：`uvicorn … --workers 1`。锁文件是该目录下的 `instance.lock`（默认 `.data/instance.lock`）。第二个写进程拿不到锁就退出，不要改成共享写。
- `RUN_WORKERS`（默认 2）是**进程内**同时执行的工单数，不是多个 uvicorn worker，也不是第二份 API。
- 工单库 `tickets.db` 与运行库 `runtime.db` 都是这份目录里的 SQLite。不要两个容器写同一份库。容量锁定见 `docs/DECISIONS.md`。
- 只有用户明确要求**同时跑两个写进程**时，才另开 Postgres + 租约。在那之前不要换存储。

## 镜像非 root 与 Trivy

见 `docs/SECURITY_CI.md`。

- 运行用户 `appuser`，uid **10001**。`Dockerfile` 最终是 `USER appuser`，其后不得再 `USER root`。
- `deploy/docker-compose.yml` 不得写 `user: root` 或 `user: 0`。不要 `docker run -u 0`。
- 挂载数据卷时，目录要对 uid 10001 可写。
- Trivy 扫 CRITICAL/HIGH，`exit-code: "1"` 保持开着。未修复漏洞用已有的 `ignore-unfixed`，不要改成只 warning 仍合并。
- 构建时卸掉 pip，避免把 pip 自带 SBOM 里的旧包算进镜像。做法见 `docs/SECURITY_CI.md`。

## 动到这些面时先读文档

`docs/P1_IDENTITY.md`、`docs/SECRETS.md`、`docs/DECISIONS.md`、`docs/SECURITY_CI.md`、`docs/GATEWAY.md`。网关 TLS 不等于多副本；API 不对外 publish。
