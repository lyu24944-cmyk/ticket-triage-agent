# P5 运行手册：暂停恢复与追踪

## 这次运行存了什么

- 等人审的工单状态在 SQLite（默认 `.data/runtime.db` 的 `pending_runs`）
- 已受理的 HTTP 运行在 `run_jobs`。`POST /runs` 返回 202 后，用返回的 `trace_id` 调 `GET /runs/{trace_id}` 看进度
- 每个节点的耗时和状态在同一库的 `trace_spans`。节点名是 `ingest`、`classify`、`act`、`draft`、`hitl_gate`、`finalize`。`act` 每走一步记一条
- 内存里的待办只是缓存。进程重启后，`/runs/human` 会从 SQLite 读回。没跑完的 `run_jobs` 标成 `failed`，错误码 `run_interrupted`，不会从中间接着跑

## 查看一次运行

创建工单的返回里有 `trace_id`：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/traces/<trace_id> -Headers @{ "X-API-Key" = $env:REVIEW_API_KEY }
```

返回字段：

| 字段 | 含义 |
|------|------|
| `spans` | 节点名、耗时毫秒、状态 |
| `total_ms` | 各节点耗时之和 |
| `over_budget` | 是否超过 `TRACE_BUDGET_MS`（默认 2000） |

日志里搜 `trace_id=` 可以对照同一次运行。

## 重启后恢复人审

1. 轮询 `GET /runs/{trace_id}`，确认 `status` 已是 `awaiting_human`，记下 `ticket_id`。创建接口本身返回的是 `running`
2. 停掉 API 再启动
3. 再调 `POST /runs/human`，带 `X-API-Key`、`ticket_id` 和 `reviewer`

没有暂停记录时接口返回 HTTP 404，正文里的错误码是 `no_pending_run`。

## 出了问题先看哪里

| 现象 | 先看 |
|------|------|
| 重启后人审失败 | `pending_runs` 里有没有这个 `ticket_id`。还在跑的单不在这张表 |
| 草稿没有引用 | 节点名是 `act`。工具抛错时这条 span 的 `status` 为 `enrich_error`，`detail` 含 `enrich_failed`。类别是 other，或这一步选择了 `finish` 时不会检索，引用为空是预期结果 |
| 接口变慢 | `over_budget` 以及耗时最高的 `node`。模型时间主要在 `classify`、`act`、`draft` |
| 工具超时 | `act` span 的 `detail` 是否含 `enrich_failed`。这是工具异常的错误码，节点名已经是 `act` |
| 创建后一直是 running | `GET /runs/{trace_id}`。进程重启过则 status 为 `failed`，errors 含 `run_interrupted` |
| 第二个进程起不来 | 数据目录的 `instance.lock` 是否已被占用。`GET /health` 里 `single_process.ok` 应为 true；库不可写或问答服务不可用时该接口返回 503 |

## 环境变量

```text
RUNTIME_DB_PATH=.data/runtime.db
TRACE_BUDGET_MS=2000
REVIEW_API_KEY=
```

API 只启动一个进程。第二个进程会因为 `instance.lock` 退出，不要对同一个数据目录开多个 worker。

SQLite 和日志在没配 Langfuse 时仍然够用。配了 `LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY` 和 `LANGFUSE_BASE_URL` 之后，同一次运行还会送到 Langfuse：按 `prompt_version` 过滤 version 或 tag，用 `latency_ms` 看延迟，用 `no_model_fallback` 和黄金集得分看质量。
