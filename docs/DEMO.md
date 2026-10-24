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
