# P5 运行手册：暂停恢复与追踪

## 这次运行存了什么

- 等人审的工单状态在 SQLite（默认 `.data/runtime.db` 的 `pending_runs`）
- 每个节点的耗时和状态在同一库的 `trace_spans`
- 内存里的待办只是缓存。进程重启后，`/runs/human` 会从 SQLite 读回

## 查看一次运行

创建工单的返回里有 `trace_id`：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/traces/<trace_id>
```

返回字段：

| 字段 | 含义 |
|------|------|
| `spans` | 节点名、耗时毫秒、状态 |
| `total_ms` | 各节点耗时之和 |
| `over_budget` | 是否超过 `TRACE_BUDGET_MS`（默认 2000） |

日志里搜 `trace_id=` 可以对照同一次运行。

## 重启后恢复人审

1. 确认创建时 `status` 是 `awaiting_human`，记下 `ticket_id`
2. 停掉 API 再启动
3. 再调 `POST /runs/human`，`ticket_id` 不变

若返回 `not_found` / `no_pending_run`：这张工单没有暂停记录，或已经被审过并删除。

## 出了问题先看哪里

| 现象 | 先看 |
|------|------|
| 重启后人审失败 | `pending_runs` 里有没有这个 `ticket_id` |
| 草稿没有引用 | 追踪里 `enrich` 的 `status` 是不是 `enrich_error` |
| 接口变慢 | `over_budget` 以及耗时最高的 `node` |
| 工具超时 | span 的 `detail` 是否含 `enrich_failed` |

## 环境变量

```text
RUNTIME_DB_PATH=.data/runtime.db
TRACE_BUDGET_MS=2000
```

Langfuse 的 Key 仍保留在配置里。当前追踪以 SQLite 和日志为准，没有 Key 也能回放。
