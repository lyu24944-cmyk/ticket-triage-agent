# 工单分流 Agent（P1 + P2 MCP）

面向 AI Agent 工程师作品集的 LangGraph **工单分流**工作流：分类 → 工具 enrichment → 起草回复 → **人工审核（HITL）** → 结案/升级。

- 锁定决策：见 [docs/DECISIONS.md](docs/DECISIONS.md)
- P2 MCP 用法：见 [docs/MCP.md](docs/MCP.md)
- P2/P3 验收：见 [docs/P2_MCP_P3_EVALS.md](docs/P2_MCP_P3_EVALS.md)
- 第一周清单：见 [docs/WEEK1_CHECKLIST.md](docs/WEEK1_CHECKLIST.md)
- 作品集路线：见 [docs/PORTFOLIO_ROADMAP.md](docs/PORTFOLIO_ROADMAP.md)
- P6 检索改调问答服务：见 [docs/P6.md](docs/P6.md)

## 架构

```mermaid
flowchart TD
  client[HTTP 客户端] --> api[FastAPI]
  api --> ingest
  ingest --> classify
  classify --> enrich
  enrich -->|失败且未超过重试| enrich
  enrich --> draft
  draft --> hitl[hitl_gate]
  hitl -->|awaiting_human| pause[(SQLite pending_runs)]
  hitl -->|auto_approve| finalize
  human[POST /runs/human] --> pause
  human --> finalize
  finalize --> done[resolved 或 escalated]
  enrich --> tools{工具面}
  finalize --> tools
  tools -->|USE_MCP=false| local[进程内工具]
  tools -->|USE_MCP=true| mcp[MCP stdio]
  local --> store[(知识库与 tickets.db)]
  mcp --> store
  nodes[各节点] --> spans[(trace_spans)]
  spans --> traces[GET /traces]
```

```text
START → ingest → classify → enrich ⇄ (重试) → draft → hitl_gate
                                                         ├─ awaiting_human（写入 SQLite 后暂停）
                                                         └─ finalize → END

enrich / finalize 工具面：
  未设置 RAG_BASE_URL、USE_MCP=false → 进程内关键词工具
  RAG_BASE_URL=http://127.0.0.1:8001 → 知识库检索改调 citation-rag-agent
  USE_MCP=true → MCP stdio 客户端 → python -m mcp_server
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

停进程后再恢复的完整演示：`powershell -File .\scripts\demo_hitl.ps1`，记录见 [docs/DEMO.md](docs/DEMO.md)。

### 用真实模型起草

默认是 stub，测试和 CI 不需要 Key。设置后，草稿由模型撰写，分类仍用 stub：

```powershell
$env:LLM_PROVIDER="anthropic"   # 或 openai
$env:ANTHROPIC_API_KEY="..."    # openai 则设 OPENAI_API_KEY，可选 OPENAI_BASE_URL
python evals/run_evals.py
```

提示里只给本次检索到的文档。模型返回的引用编号不在其中时会被删掉；没有检索结果时引用为空。模型这条路径上，任何一张工单退回 stub，评测都会失败；延迟只记录，不卡 2 秒预算。

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
| 知识库 / 工具报错 / MCP 超时 / 问答服务连不上 | 最多重试 `max_tool_retries` 次，然后无引用起草 |
| 模型起草报错或输出不是 JSON | 退回 stub 草稿并记 `draft_model_failed`，仍进人审；评测里视为不通过 |
| 模型写了检索结果以外的文档编号 | 该编号从引用里删掉 |
| 低置信度草稿 | 默认仍进人审；人可改稿 |
| 人审驳回 | 状态为 `escalated`，不会静默丢弃 |
| 非法 status | MCP/处理器返回 `status_not_allowed` |
| 无待审运行 | 返回 `not_found` + 错误码 |

## 简历话术模板

> 用 LangGraph 搭建工单分流 Agent：类型化工具经 MCP 暴露（3 工具、写操作白名单）、HITL 审批、工具超时软降级；知识库检索可改调带引用的问答服务，对不上文档时不编造引用；FastAPI 暴露 run API；黄金集评测 CI 门禁。

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
3. **P3** — 黄金 JSONL + CI 门禁（含不得伪造引用）
4. **P5** — 人审状态落 SQLite，重启可恢复；`GET /traces/{trace_id}` 看节点耗时
5. **P6** — 设置 `RAG_BASE_URL` 后，知识库检索改调 citation-rag-agent（已完成，见 `docs/P6.md`）
