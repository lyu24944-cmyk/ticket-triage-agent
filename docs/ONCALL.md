# 值班说明（一页）

先看日志和 `GET /health`，不上完整 APM。CI 仍是 stub 的 `pytest` 加黄金评测，**不把这些日志当成合并门槛**。

告警通道（Webhook / 邮件）见下方「告警外发」；处置表不变。

## 日志怎么搜

结构化日志在 logger `ticket_triage.ops`，一行一个事件，固定键：

```text
event=... trace_id=... ticket_id=... node=... queue_depth=... stub_fallback=0|1|- tool_error=...|- model_failed=...|- alert_code=...|-
```

| 键 | 含义 |
|----|------|
| `event` | `accepted` / `queue_full` / `node_done` / `node_error` / `run_done` |
| `node` | `ingest` `classify` `act` `draft` `hitl_gate` `finalize` |
| `queue_depth` | 当前已受理未结束的单数（与 health 的 `active` 同口径） |
| `stub_fallback=1` | 本步退回了 stub（分类 / 调度 / 起草） |
| `tool_error` | 工具失败码，通常是 `enrich_failed`（节点名仍是 `act`） |
| `model_failed` | `classify_model_failed` / `act_model_failed` / `draft_model_failed` |
| `alert_code` | 可告警码：`queue_full` / 上述 model_failed / `enrich_failed`；无则 `-` |

`OPS_LOG_JSON=true` 时同一字段打成 **一行 JSON**，便于 Fluent Bit / 云日志采集（集中日志），不必上完整 APM。

```powershell
# 例子
Select-String -Path .\api.log -Pattern "event=queue_full|alert_code=|stub_fallback=1|tool_error=enrich_failed"
Invoke-RestMethod http://127.0.0.1:8000/health
```

`GET /health` → `checks.queue`：`active` / `limit` / `workers`。

## 告警外发（邮件 / IM）

| 变量 | 含义 |
|------|------|
| `ALERT_WEBHOOK_URL` | 飞书 / 钉钉 / Slack / 通用入站 URL；POST JSON |
| `ALERT_COOLDOWN_SECONDS` | 同一 `alert_code` 冷却（默认 60），打满队列不刷屏 |
| `ALERT_EMAIL_TO` + `ALERT_SMTP_HOST` | 可选 SMTP；可与 Webhook 同时开 |
| `OPS_LOG_JSON` | `true` 时 ops 行为 JSON，进集中日志 |

触发：`queue_full`、`classify_model_failed`、`act_model_failed`、`draft_model_failed`、`enrich_failed`、`closure_callback_failed`。未配置 URL/SMTP 时只写日志。

验收：设 Webhook（或测试 hook）后把 `RUN_QUEUE_LIMIT` 调小并打满，应出现 `alert_code=queue_full` 且通道收到一条。

## 告警码：先看哪里

| 看到 | 先看 | 再做什么 |
|------|------|----------|
| `queue_full` | `GET /health` 的 `checks.queue.active` 是否顶到 `limit`；日志里同分钟的 `queue_depth` | 等 `active` 降下来再受理。同一张已在队列里的单再提交会返回原 `trace_id`，不是新单。不要加副本抢同一份 SQLite |
| `classify_model_failed` | 日志 `node=classify stub_fallback=1` / `model_failed=`；`GET /runs/{trace_id}` 或待审列表的 `errors` | 流程多半还在继续（关键词分类）。查模型 Key、超时、`LLM_PROVIDER` / Base URL。评测门禁会因此失败；值班先保服务，再查退回比例 |
| `act_model_failed` | 日志 `node=act stub_fallback=1`；同 `trace_id` 的后续 `act` 是否仍在跑 | 调度已改走固定规则。查模型 JSON/超时。工具白名单错误是 `tool_not_allowed`，不是这条 |
| `draft_model_failed` | 日志 `node=draft stub_fallback=1`；审核台草稿是否还能人审 | 已退回 stub 草稿，通常仍停在 `awaiting_human`。先让审核员处理，再查模型 |
| `enrich_failed` | 日志 `node=act tool_error=enrich_failed`；`GET /traces/{trace_id}` 里 `act` span 的 `detail` | 工具超时或下游挂了（MCP / `RAG_BASE_URL`）。`GET /health` 看 `checks.rag`。软降级后仍可能起草，引用可能为空——先修检索，不要改图节点名 |
| `closure_callback_failed` | 日志 `event=closure_callback_dead` / `closure_callback_failed`；runtime 表 `closure_callbacks` 里 `status=dead` | 结案已成功，出站回调耗尽重试。查对端 URL、签名密钥是否与进线 Webhook 一致。不要回滚工单状态；修好下游后可手工重放或等运维补投 |

## 不要做的事

- 不要为了这些码去改 CI 或加合并检查
- 不要把 `enrich_failed` 当成已删除的 `enrich` 节点
- 不要在未换存储前对同一数据目录起第二个 API 进程
