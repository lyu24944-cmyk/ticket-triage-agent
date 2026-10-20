# 工单分流 Agent（P1）

面向 AI Agent 工程师作品集的 LangGraph **工单分流**工作流：分类 → 工具 enrichment → 起草回复 → **人工审核（HITL）** → 结案/升级。

- 锁定决策：见 [docs/DECISIONS.md](docs/DECISIONS.md)
- P2 MCP + P3 评测计划：见 [docs/P2_MCP_P3_EVALS.md](docs/P2_MCP_P3_EVALS.md)
- 第一周清单：见 [docs/WEEK1_CHECKLIST.md](docs/WEEK1_CHECKLIST.md)
- 作品集路线：见 [docs/PORTFOLIO_ROADMAP.md](docs/PORTFOLIO_ROADMAP.md)

## 架构

```text
START → ingest → classify → enrich ⇄ (重试) → draft → hitl_gate
                                                         ├─ awaiting_human（暂停）
                                                         └─ finalize → END
```

| 节点 | 职责 |
|------|------|
| `ingest` | 写入工单仓库 |
| `classify` | 优先级 + 类别（默认 stub LLM） |
| `enrich` | 知识库检索 + 工单历史；失败可软降级重试 |
| `draft` | 回复草稿 + 引用 |
| `hitl_gate` | 除非 `auto_approve`，否则暂停等人审 |
| `finalize` | 结案或升级 |

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

## 失败模式（面试可讲）

| 失败 | 行为 |
|------|------|
| 知识库 / 工具报错 | 最多重试 `max_tool_retries` 次，然后无引用起草 |
| 低置信度草稿 | 默认仍进人审；人可改稿 |
| 人审驳回 | 状态为 `escalated`，不会静默丢弃 |
| 无待审运行 | 返回 `not_found` + 错误码 |

## 简历话术模板

> 用 LangGraph 搭建工单分流 Agent：类型化工具、HITL 审批、工具失败软降级；FastAPI 暴露 run API；stub LLM 支持离线测试；路线图含 MCP 工具服务与 golden-set CI 评测门禁。

## 目录结构

```text
src/ticket_triage/     # 业务代码
tests/                 # 单测（无需 API Key）
evals/                 # P3 黄金集与打分（脚手架）
mcp_server/            # P2 MCP 封装（脚手架）
docs/                  # 决策、第一周、P2/P3 验收
```

## 路线图

1. **P1（本仓库）** — 状态图 + API + 测试  
2. **P2** — 通过 MCP 暴露 KB/工单工具；Agent 只调 MCP  
3. **P3** — 黄金 JSONL + CI 门禁  
4. **P4/P5** — 可选：带引用 RAG / 长任务异步流水线
