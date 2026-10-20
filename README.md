# 工单分流 Agent（P1 + P2 MCP）

面向 AI Agent 工程师作品集的 LangGraph **工单分流**工作流：分类 → 工具 enrichment → 起草回复 → **人工审核（HITL）** → 结案/升级。

- 锁定决策：见 [docs/DECISIONS.md](docs/DECISIONS.md)
- P2 MCP 用法：见 [docs/MCP.md](docs/MCP.md)
- P2/P3 验收：见 [docs/P2_MCP_P3_EVALS.md](docs/P2_MCP_P3_EVALS.md)
- 第一周清单：见 [docs/WEEK1_CHECKLIST.md](docs/WEEK1_CHECKLIST.md)
- 作品集路线：见 [docs/PORTFOLIO_ROADMAP.md](docs/PORTFOLIO_ROADMAP.md)

## 架构

```text
START → ingest → classify → enrich ⇄ (重试) → draft → hitl_gate
                                                         ├─ awaiting_human（暂停）
                                                         └─ finalize → END

enrich / finalize 工具面：
  USE_MCP=false → 进程内工具
  USE_MCP=true  → MCP stdio 客户端 → python -m mcp_server
```

| 节点 | 职责 |
|------|------|
| `ingest` | 写入共享 SQLite 工单库 |
| `classify` | 优先级 + 类别（默认 stub LLM） |
| `enrich` | 知识库检索 + 工单历史；失败可软降级重试 |
| `draft` | 回复草稿 + 引用 |
| `hitl_gate` | 除非 `auto_approve`，否则暂停等人审 |
| `finalize` | 结案或升级（经网关写状态） |

## 快速开始

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
uvicorn ticket_triage.api.main:app --reload --port 8000
```

发起一次运行：

```bash
curl -X POST http://127.0.0.1:8000/runs -H "Content-Type: application/json" -d "{\"ticket\":{\"ticket_id\":\"t-1\",\"subject\":\"Urgent refund\",\"body\":\"Charged twice\"},\"auto_approve\":false}"
```

人工通过 / 驳回：

```bash
curl -X POST http://127.0.0.1:8000/runs/human -H "Content-Type: application/json" -d "{\"ticket_id\":\"t-1\",\"approved\":true}"
```

### 开启 MCP 工具面

```powershell
$env:USE_MCP="true"
uvicorn ticket_triage.api.main:app --reload --port 8000
```

单独启动 MCP Server（给 Cursor / Claude Desktop）：

```powershell
python -m mcp_server
```

配置片段与安全说明见 [docs/MCP.md](docs/MCP.md)。

## 安全说明（工具面）

- 仅 3 个工具：`search_knowledge_base`、`get_ticket_history`、`update_ticket_status`
- **禁止**：shell、任意文件读写、任意 SQL
- 写操作 `status` 白名单：`open|resolved|escalated|pending`，并打审计日志
- MCP 调用默认 5 秒超时；超时记 `enrich_failed` 后软降级起草

## 失败模式（面试可讲）

| 失败 | 行为 |
|------|------|
| 知识库 / 工具报错 / MCP 超时 | 最多重试 `max_tool_retries` 次，然后无引用起草 |
| 低置信度草稿 | 默认仍进人审；人可改稿 |
| 人审驳回 | 状态为 `escalated`，不会静默丢弃 |
| 非法 status | MCP/处理器返回 `status_not_allowed` |
| 无待审运行 | 返回 `not_found` + 错误码 |

## 简历话术模板

> 用 LangGraph 搭建工单分流 Agent：类型化工具经 MCP 暴露（3 工具、写操作白名单）、HITL 审批、工具超时软降级；FastAPI 暴露 run API；共享 SQLite 供 Agent/MCP 子进程一致读写；黄金集评测 CI 门禁。

## 目录结构

```text
src/ticket_triage/     # 业务代码（含 MCP 客户端网关）
mcp_server/            # MCP Server（stdio）
tests/                 # 单测 + MCP integration
evals/                 # P3 黄金集与打分
docs/                  # 中文文档
```

## 路线图

1. **P1** — 状态图 + API + 测试（已完成）
2. **P2** — MCP 工具服务 + `USE_MCP`（本阶段）
3. **P3** — 黄金 JSONL + CI 门禁（脚手架已有，可继续加回归）
4. **P4/P5** — 可选：带引用 RAG / 长任务异步流水线
