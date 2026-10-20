# P2 MCP + P3 评测 — 接入顺序与验收标准

## 接入顺序（不要颠倒）

```text
P1 稳定（pytest 绿、HITL 可用）
    → P2a：MCP Server 封装现有工具（kb + tickets）
    → P2b：Agent 的 enrich/finalize 通过 MCP 客户端调用（特性开关）
    → P3a：黄金 JSONL + 确定性打分（本地）
    → P3b：CI 在分数低于阈值时让 PR 失败
    → P3c：可选 LLM-as-judge + Langfuse 追踪
```

原因：评测必须对准**稳定的工具面**。若 MCP 晚于评测接入，工具 schema 一变就会打爆门禁。

---

## P2 — MCP 工具服务

### 范围

只暴露 **三个工具**（像产品一样克制）：

| 工具 | 对应实现 | 是否写操作 |
|------|----------|------------|
| `search_knowledge_base` | `tools/kb.py` | 否 |
| `get_ticket_history` | `tools/tickets.py` | 否 |
| `update_ticket_status` | `tools/tickets.py` | 是（需确认 / 白名单） |

实现目录：[`mcp_server/`](../mcp_server/)，用法见 [`MCP.md`](MCP.md)。  
**P2 状态：已实现**（`USE_MCP` + stdio Server + 共享 SQLite + 超时软降级）。

### 验收标准（P2 完成）

1. `python -m mcp_server`（或文档中的启动命令）能注册上述 3 个带 JSON Schema 的工具。  
2. `USE_MCP=true` 时，Agent 在黄金工单上产出与进程内工具相同形态的分类/草稿。  
3. 写操作拒绝未知 `status`；写操作有日志。  
4. 超时：客户端 5 秒中断工具调用；图记录 `enrich_failed` 并软降级。  
5. README 安全说明：无 shell 工具、无任意 SQL、最小权限。  
6. Demo：提供 Cursor / Claude Desktop 连接该 MCP 的配置片段。

### P2 非目标

- 对接真实 Zendesk/Jira OAuth（模拟仓库即可）  
- 从 OpenAPI 动态生成工具

---

## P3 — 评测门禁

### 范围

目录 [`evals/`](../evals/)：

- `golden/tickets.jsonl` — 输入 + 期望类别/优先级/引用规则  
- `scorers.py` — 确定性指标  
- `run_evals.py` — CLI，失败时 exit code 1  
- `.github/workflows/evals.yml` — PR 门禁

### 最低指标

| 指标 | 通过线 |
|------|--------|
| `category_accuracy` | ≥ 0.85 |
| `priority_accuracy` | ≥ 0.75 |
| `citation_present_when_kb_hit` | ≥ 0.90（应命中 KB 时） |
| `no_empty_draft` | 100% |
| `p95_latency_ms`（本地 stub） | < 2000 |

后续可选：语气的 LLM-as-judge（校准前不做合并阻断）。

### 验收标准（P3 完成）

1. `python evals/run_evals.py` 打印各指标，并以 0/1 退出。  
2. CI 在 pull_request 上运行；红灯阻止合并。  
3. 至少 1 条来自已知 bug 的**回归用例**（例如空 query 不得伪造引用）。  
4. 本文「如何决定发版/不发版」小节写清规则。  
5. 评测报告 JSON 中记录 prompt/工具版本号。

### 发版 / 不发版

- **发版**：所有确定性门禁绿灯；无新增高危失败模式。  
- **不发版**：相对 main，`category_accuracy` 下降超过 5 个百分点，或任意 `no_empty_draft` 失败。  
- 软性 judge 失败 → 开 issue，在与人标一致率达标前不阻断合并。

---

## 周计划映射

| 周次 | 里程碑 |
|------|--------|
| 4 | P2a：MCP Server 可运行 |
| 5 | P2b：enrich 上特性开关接入 MCP 客户端 |
| 6 | P3a：本地黄金评测 |
| 7–8 | P3b：CI 门禁 + 回归用例 |

---

## 完成后简历话术（P2/P3）

> 将分流工具暴露为 MCP Server（3 个类型化工具、写操作白名单），并用黄金集评测在 CI 中做发版门禁（类别准确率 ≥ 0.85、引用检查、延迟预算）。
