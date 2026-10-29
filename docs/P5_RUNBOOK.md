# P5 运行手册：暂停恢复与追踪

## 这次运行存了什么

- 等人审的工单状态在 SQLite（默认 `.data/runtime.db` 的 `pending_runs`）
- 已受理的 HTTP 运行在 `run_jobs`。里面有原始请求、每个已完成节点之后的 `checkpoint_json`，以及下一步 `next_node`。`POST /runs` 和 `GET /runs/{trace_id}` 要带受理密钥 `SERVICE_API_KEY`。返回 202 后用 `trace_id` 看进度
- 每个节点的耗时和状态在同一库的 `trace_spans`。节点名是 `ingest`、`classify`、`act`、`draft`、`hitl_gate`、`finalize`。`act` 每走一步记一条
- 内存里的待办只是缓存。进程重启后，`/runs/human` 会从 SQLite 读回。没跑完的单从 `next_node` 继续。正在执行的那个节点会再跑一次；已经写进检查点的节点不重跑。没有请求正文的旧行才会标成 `failed` / `run_interrupted`
- 表结构版本在 `schema_migrations`。当前是 1（初始表）和 2（可恢复任务的列）。旧库在启动时补列，不要手改表

## 单实例容量

这条服务按一个进程上线。`uvicorn --workers 1`。同一数据目录的第二个进程会被 `instance.lock` 拒绝。不要把同一份 SQLite 挂给多个副本。

| 设置 | 默认 | 行为 |
|------|------|------|
| `RUN_WORKERS` | 2 | 这个进程里同时执行几张单 |
| `RUN_QUEUE_LIMIT` | 32 | 已受理但还没结束的单，含正在执行的。按受理时间排队 |

排队时 `GET /runs/{trace_id}` 的 `status` 仍是 `running`。没有排队超时。超过上限的新工单返回 HTTP 429，错误码 `queue_full`，不会写入。同一张已经在队列里的单再提交，返回原来的 `trace_id`。`GET /health` 的 `checks.queue` 有 `active`、`limit`、`workers`。领取顺序是 `created_at`（含毫秒）再按入库 `rowid`；同一秒受理的多张单仍按提交先后，不看 `trace_id`。

正常退出会再等约 15 秒。这段时间里没跑完的单留在库里，下次启动从最后完成的节点继续。被打断的节点可能在追踪里出现两次；它前面的节点不会重做。入库是「已有则跳过」。检索和读历史可以重做。结案如果已经写上同一状态，不会再追加一条历史。

写检查点或保存最终结果抛错时：**放下领取标记**，单保持可领，别的工人（或同进程下一轮）从上次检查点接着跑；不因此标成 `failed`。图本身跑崩仍会写成 `failed` / `internal_error` 并放开名额。工人线程意外退出时会立刻补员，活着的工人数保持为 `RUN_WORKERS`。

**范围：** 节点检查点续跑只覆盖 HTTP `POST /runs`。`evals/run_evals.py`、`scripts/demo_p6.py` 等直接调 `run_triage` 的路径仍是一次跑完，杀进程不会从节点中间恢复。

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

值班搜日志与 `queue_full` / `*_model_failed` / `enrich_failed` 的处置见 [ONCALL.md](ONCALL.md)。

## 出了问题先看哪里

| 现象 | 先看 |
|------|------|
| 重启后人审失败 | `pending_runs` 里有没有这个 `ticket_id`。还在跑的单不在这张表 |
| 草稿没有引用 | 节点名是 `act`。工具抛错时这条 span 的 `status` 为 `enrich_error`，`detail` 含 `enrich_failed`。类别是 other，或这一步选择了 `finish` 时不会检索，引用为空是预期结果 |
| 接口变慢 | `over_budget` 以及耗时最高的 `node`。模型时间主要在 `classify`、`act`、`draft` |
| 工具超时 | `act` span 的 `detail` 是否含 `enrich_failed`。这是工具异常的错误码，节点名已经是 `act` |
| 创建后一直是 running | `GET /health` 的 `checks.queue.active`。未超过 `RUN_WORKERS` 时应该在往下走。进程重启后用同一个 `trace_id` 再查，应从上次节点继续，而不是 `run_interrupted` |
| 新工单被拒绝 | HTTP 429 且 `errors` 为 `queue_full`。等 `active` 降下来，或只对已经受理的 `trace_id` 轮询 |
| 第二个进程起不来 | 数据目录的 `instance.lock` 是否已被占用。`GET /health` 里 `single_process.ok` 应为 true；库不可写或问答服务不可用时该接口返回 503 |

## 环境变量

```text
RUNTIME_DB_PATH=.data/runtime.db
TRACE_BUDGET_MS=2000
RUN_WORKERS=2
RUN_QUEUE_LIMIT=32
SERVICE_API_KEY=
REVIEW_API_KEY=
# 生产优先挂文件；轮换见 docs/SECRETS.md
# SERVICE_API_KEY_FILE=/run/secrets/service_api_key
# REVIEW_API_KEY_FILE=/run/secrets/review_api_key
# SERVICE_API_KEY_PREVIOUS=
# REVIEW_API_KEY_PREVIOUS=
```

API 只启动一个进程。第二个进程会因为 `instance.lock` 退出，不要对同一个数据目录开多个 worker。密钥轮换与禁止进镜像/日志见 [SECRETS.md](SECRETS.md)。

## 备份和恢复演练

定时冷备份、异地目录、**RPO ≤ 24h / RTO ≤ 1h** 与季度演练清单见 [BACKUP.md](BACKUP.md)。脚本：`scripts/backup_sqlite.py`（先 `wal_checkpoint` 再拷）。

先停掉这一个 API 进程，再复制文件。不要复制 `instance.lock`。正常退出会把 WAL 收进主库；若旁边还有 `tickets.db-wal`、`tickets.db-shm`、`runtime.db-wal`、`runtime.db-shm`，和主文件一起拷。

1. `GET /health`，确认 `ok` 为 true，记下 `checks.queue`。
2. 停进程。
3. 把 `.data/tickets.db` 和 `.data/runtime.db` 复制到备份目录。有 `-wal` / `-shm` 就一起复制。
4. 启动 API。抽一张待审单，`POST /runs/human` 仍能结案。
5. 再停进程，用备份覆盖这两个库，启动。同一张单还在，`schema_migrations` 里有版本 2。
6. 中断演练：再提交一张单，在 `status` 仍是 `running` 时结束进程。启动后用同一个 `trace_id` 轮询，应变成 `awaiting_human`，而不是 `failed` / `run_interrupted`。`GET /traces/{trace_id}` 里，杀掉之前已经完成的节点还在。

### 演练记录（2026-10-27）

完整表格见 [DEMO.md](DEMO.md)「2026-10-27 备份与杀进程演练」。摘要：

- 备份：`drill-backup-155954` / `552cd769-5b16-4265-9ae9-4b95873a4e22`。停进程 → 拷贝 `tickets.db`、`runtime.db` → 覆盖回来后仍是 `awaiting_human`；`schema_migrations` 为 1 与 2。
- 杀进程：`drill-kill-160027` / `f4633848-21b0-4141-a028-6df8ca9c86ab`。杀掉时 `running` 且 `next_node=ingest`、尚无 span；重启后同一 `trace_id` 变为 `awaiting_human`。追踪为 `ingest` → `classify` → `act`×3 → `draft` → `hitl_gate`；因中断重跑的是尚未落盘的 `ingest`，`act`×3 是工具循环不是重复 span。

SQLite 和日志在没配 Langfuse 时仍然够用。配了 `LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY` 和 `LANGFUSE_BASE_URL` 之后，同一次运行还会送到 Langfuse：按 `prompt_version` 过滤 version 或 tag，用 `latency_ms` 看延迟，用 `no_model_fallback` 和黄金集得分看质量。
