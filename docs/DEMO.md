# 人审演示记录

用 `scripts/demo_hitl.ps1` 跑通：创建工单并停在人工审核，停掉 API，再启动后用同一个 `ticket_id` 结案。

2026-10-21 本地实跑，工单 `demo-60s`，`trace_id` 为 `c9356308-7df2-4c95-a1ca-38eb62ce49e8`。

## 重跑

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo_hitl.ps1
```

脚本会自己启动和停掉 `127.0.0.1:8000`。若该端口已被占用，先停掉旧进程。脚本把 `RAG_BASE_URL` 设为 `local`，检索用仓库里的三篇关键词文档，不访问 `.env` 里的远程地址。模型仍由 `.env` 的 `LLM_PROVIDER` 决定。

## 2026-10-24 实跑

工单 `demo-60s`，`trace_id` 为 `2235f13c-a0c4-451a-9a06-b69b1cc02729`。模型是 `.env` 里的 DeepSeek。

1. 创建后状态是 `awaiting_human`，当时不在已处理列表里。分类 `billing` / `high`。工具依次是 `search_knowledge_base`、`get_ticket_history`、`finish`，都成功。引用 `kb-billing-refund`。类别、优先级、引用、非法工具、工具选择得分都是 1.0，没有退回 stub。
2. 停掉 API 后重新启动，对同一个 `demo-60s` 提交 `approved: true`。状态变为 `resolved`，`trace_id` 不变。状态历史是 `open` → `resolved`。
3. 问候单 `demo-hello`（`48461170-44db-42ad-a7f4-27e1840610a4`）分类 `other` / `low`，只调用 `finish`，引用为空，仍停在 `awaiting_human`。工具选择和「不伪造引用」都是 1.0。
4. `GET /traces/2235f13c-a0c4-451a-9a06-b69b1cc02729`：`hitl_gate` 在 `2026-10-24T10:15:03Z` 为 `awaiting_human`，`finalize` 在 `2026-10-24T10:15:07Z` 为 `resolved`。节点合计约 9549 ms，`over_budget` 为 true。超出的是模型耗时，检索本身成功。

## 2026-10-21 的 stub 记录

同一脚本当时还没有强制 `local`，分类走关键词 stub。`trace_id` 为 `c9356308-7df2-4c95-a1ca-38eb62ce49e8`。`over_budget` 为 false，合计约 18.8 ms。

## 2026-10-27 备份与杀进程演练

按 `docs/P5_RUNBOOK.md`「备份和恢复演练」在本地实跑。模型是 `.env` 的 `openai` / `deepseek-flash`，`RAG_BASE_URL=local`。不改架构。

### 备份 / 覆盖

| 项 | 值 |
|----|----|
| 日期 | 2026-10-27 15:59:52 +08:00 |
| `GET /health` | `ok=true`，`checks.queue` active=0 limit=32 workers=2 |
| ticket_id | `drill-backup-155954` |
| trace_id | `552cd769-5b16-4265-9ae9-4b95873a4e22` |
| 备份目录 | `.data/backups/drill-20261027-160022`（`tickets.db`、`runtime.db`；未拷 `instance.lock`） |

1. 提交后退到 `awaiting_human`。停掉唯一 API 进程，复制两个库。
2. 再启动：同一 `trace_id` 仍是 `awaiting_human`；`GET /runs/pending` 当时共 11 张待审。
3. 再停进程，用备份覆盖 `.data/tickets.db` 与 `.data/runtime.db` 后启动：同一张单仍是 `awaiting_human`。`schema_migrations` 为 `[1, 2]`。

### 杀进程 / 续跑

| 项 | 值 |
|----|----|
| ticket_id | `drill-kill-160027` |
| trace_id | `f4633848-21b0-4141-a028-6df8ca9c86ab` |
| 杀掉时 status | `running` |
| 杀掉时检查点 | `next_node=ingest`，`trace_spans` 为空（首个节点尚未落盘） |
| 重启后 status | `awaiting_human`（不是 `failed` / `run_interrupted`） |
| 追踪合计 | 约 19683 ms，`over_budget=true` |

重启后 `GET /traces/f4633848-21b0-4141-a028-6df8ca9c86ab` 的节点顺序：

`ingest` → `classify` → `act` → `act` → `act` → `draft` → `hitl_gate`

- **因中断而重跑的节点：** 杀掉时停在 `ingest` 且没有已完成 span，重启后从 `ingest` 整段再跑；没有「杀掉前已完成、杀掉后又出现第二份」的节点。
- **`act` 出现 3 次：** 是同一次续跑里工具循环的三步（两次 `acting`、一次 `enriched`），不是杀进程导致的重复 span。
