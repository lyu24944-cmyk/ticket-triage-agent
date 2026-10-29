# P0 演示验收清单

目标：**先能安全扛真实流量**。仍是**单实例**（不要 scale API / 双写同一 SQLite）。  
每条可单独勾选；自动化部分约 1 分钟，手工演练按切片选做。

## 0. 一键自动门禁（开场必跑）

在仓库根目录、已 `pip install -e ".[dev]"` 的 venv：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_secrets.py `
  tests/test_gateway_config.py `
  tests/test_image_security.py `
  tests/test_backup_sqlite.py `
  tests/test_alerts.py `
  tests/test_ops_log.py `
  -q
```

| 通过标准 | ☐ 全部 green |
|----------|--------------|

可选（静态非 root，不启 Docker）：

```powershell
.\.venv\Scripts\python.exe tests\check_dockerfile_nonroot.py
```

| 通过标准 | ☐ 打印 `OK: Dockerfile ends as USER appuser (uid 10001)` |
|----------|----------------------------------------------------------|

---

## P0.1 密钥轮换与禁入镜像/日志

详细步骤：[SECRETS.md](SECRETS.md)

| # | 动作 | 期望 | ☐ |
|---|------|------|---|
| 1 | `pytest tests/test_secrets.py -q` | 全过 | |
| 2 | 设 `SERVICE_API_KEY` + `SERVICE_API_KEY_PREVIOUS`，新旧钥各 `POST /runs` | 均 202 | |
| 3 | 清空 PREVIOUS，再用旧钥 | 401 `service_auth_failed` | |
| 4 | 日志 / `repr(settings)` 搜密钥明文 | 无明文；误打为 `[REDACTED_SECRET]` | |
| 5 | Dockerfile / `.dockerignore` | 无 `COPY .env`；`.env` 被 ignore | |

**切片验收（原口径）**：密钥轮换演练通过。

---

## P0.2 TLS / 内网网关 / 限流与 body

详细步骤：[GATEWAY.md](GATEWAY.md)

| # | 动作 | 期望 | ☐ |
|---|------|------|---|
| 1 | `pytest tests/test_gateway_config.py -q` | nginx 64000 / TLS / limit_req 429；compose 不 publish API 8000 | |
| 2 | （可选 Docker）`gen_gateway_certs` + `deploy/docker compose up` | `curl -k https://127.0.0.1:8443/health` → 200 | |
| 3 | （可选）body > 64000 打网关 | 413 | |
| 4 | （可选）短时间打爆 `POST /runs` | 网关 429 | |
| 5 | `docker compose port api 8000` | 失败（未映射） | |

**切片验收**：对外只经 TLS 网关；body/速率在网关统一；API 不直接对公网。

---

## P0.3 依赖/镜像扫描与非 root

详细步骤：[SECURITY_CI.md](SECURITY_CI.md)

| # | 动作 | 期望 | ☐ |
|---|------|------|---|
| 1 | `pytest tests/test_image_security.py -q` | 工作流含 pip-audit / Trivy / uid 门禁 | |
| 2 | `python tests/check_dockerfile_nonroot.py` | 最终 `USER appuser` | |
| 3 | （可选 / CI）`docker build` + `docker run --entrypoint id … -u` | `10001` | |
| 4 | （CI）`security.yml` | deps-audit + image CRITICAL/HIGH 绿 | |

**切片验收**：扫描进 CI；镜像非 root 为门禁。

---

## P0.4 备份恢复与 RPO/RTO

详细步骤：[BACKUP.md](BACKUP.md)、[P5_RUNBOOK.md](P5_RUNBOOK.md)

| # | 动作 | 期望 | ☐ |
|---|------|------|---|
| 1 | `pytest tests/test_backup_sqlite.py -q` | WAL 收口、无 `instance.lock`、异地拷贝、prune | |
| 2 | 停 API → `backup_sqlite.py --offsite <第二路径>` | 有 `MANIFEST.json`；异地同名目录 | |
| 3 | 用备份覆盖两库 → 启动 → 待审单可人审 | 恢复成功 | |
| 4 | 口头/记录 | **RPO ≤ 24h**（日备）、**RTO ≤ 1h**（本次恢复计时） | |

**切片验收（原口径）**：备份恢复演练通过。季度完整演练仍按 P5 + BACKUP 模板记一笔。

---

## P0.5 值班日志与打满队列告警

详细步骤：[ONCALL.md](ONCALL.md)

| # | 动作 | 期望 | ☐ |
|---|------|------|---|
| 1 | `pytest tests/test_alerts.py tests/test_ops_log.py -q` | 含「打满队列 → alert」 | |
| 2 | 日志行含固定键 | `alert_code` / `model_failed` / `queue_depth` 等可搜 | |
| 3 | （可选）`OPS_LOG_JSON=true` | ops 一行为 JSON，可进集中日志 | |
| 4 | （可选）配 `ALERT_WEBHOOK_URL`，调小 `RUN_QUEUE_LIMIT` 打满 | Webhook 收到 `alert_code=queue_full`（有冷却） | |

**切片验收（原口径）**：故意打满队列能告警。

---

## 演示话术（约 5 分钟）

1. **跑开场 pytest** → 说明 P0 五块都有自动化门禁。  
2. **密钥**：PREVIOUS 窗口 → 新旧都能过 → 摘掉旧钥 401；密钥不进镜像/日志。  
3. **网关**：只暴露 8443；body 64KB、限流在 nginx；API 单进程内网。  
4. **非 root + CI 扫描**：uid 10001；`security.yml`。  
5. **备份**：停服 → WAL 收口拷贝 → 异地；RPO/RTO 口径。  
6. **告警**：打满队列 → `queue_full` 日志 + Webhook；不上完整 APM。  
7. **收口**：未确认双写需求前**不加副本**。

## 原 P0 总验收对照

| 原要求 | 对应切片 | ☐ |
|--------|----------|---|
| 密钥轮换演练通过 | P0.1 | |
| 备份恢复演练通过 | P0.4（+ P5） | |
| 故意打满队列能告警 | P0.5 | |
| TLS / 内网或网关 / 限流 body | P0.2 | |
| 漏洞扫描 + 非 root 门禁 | P0.3 | |

## 不要在演示里做的事

- 不要 `docker compose scale api=2` 或第二进程写同一 `.data`
- 不要把扫描/ops 日志改成 CI 合并硬门槛以外的「评测门禁」（评测仍是 stub + 黄金集）
- 不要现场把真实密钥贴进聊天或日志
