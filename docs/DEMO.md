# 人审演示记录

用 `scripts/demo_hitl.ps1` 跑通：创建工单并停在人工审核，停掉 API，再启动后用同一个 `ticket_id` 结案。

2026-10-21 本地实跑，工单 `demo-60s`，`trace_id` 为 `c9356308-7df2-4c95-a1ca-38eb62ce49e8`。

## 重跑

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo_hitl.ps1
```

脚本会自己启动和停掉 `127.0.0.1:8000`。若该端口已被占用，先停掉旧进程。

## 这次跑到的结果

1. 创建后状态是 `awaiting_human`。分类为 `billing` / `high`，草稿引用 `kb-billing-refund`。
2. 停掉 API 进程 `18296`。内存里的待审记录消失，SQLite 里还在。
3. 重新启动后，对同一个 `demo-60s` 提交 `approved: true`，状态变为 `resolved`。`trace_id` 不变。
4. `GET /traces/c9356308-7df2-4c95-a1ca-38eb62ce49e8` 里，`hitl_gate` 在 `2026-10-21T05:05:42Z` 记为 `awaiting_human`，`finalize` 在 `2026-10-21T05:05:44Z` 记为 `resolved`。`over_budget` 为 false。

节点耗时（毫秒）：ingest 9.86，classify 0.03，enrich 1.61，draft 0.03，hitl_gate 0.01，finalize 7.26。合计约 18.8，低于 2000 的预算。
