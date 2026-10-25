# 工单分流 Agent

面向 AI Agent 工程师作品集的 LangGraph **工单分流**工作流：分类 → 白名单内自选工具 → 起草回复 → **人工审核（HITL）** → 结案/升级。

- 锁定决策：见 [docs/DECISIONS.md](docs/DECISIONS.md)
- P2 MCP 用法：见 [docs/MCP.md](docs/MCP.md)
- P2/P3 验收：见 [docs/P2_MCP_P3_EVALS.md](docs/P2_MCP_P3_EVALS.md)
- 第一周清单：见 [docs/WEEK1_CHECKLIST.md](docs/WEEK1_CHECKLIST.md)
- 作品集路线：见 [docs/PORTFOLIO_ROADMAP.md](docs/PORTFOLIO_ROADMAP.md)
- P6 检索改调问答服务：见 [docs/P6.md](docs/P6.md)
- P7 工具调用：见 [docs/P7_AGENT_PLAN.md](docs/P7_AGENT_PLAN.md)

## 架构

```mermaid
flowchart TD
  client[HTTP 客户端] --> api[FastAPI]
  api --> ingest
  ingest --> classify
  classify --> act
  act -->|未结束或工具失败可重试| act
  act --> draft
  draft --> hitl[hitl_gate]
  hitl -->|awaiting_human| pause[(SQLite pending_runs)]
  hitl -->|auto_approve| finalize
  human[POST /runs/human] --> pause
  human --> finalize
  finalize --> done[resolved 或 escalated]
  act --> tools{工具面}
  finalize --> tools
  tools -->|USE_MCP=false| local[进程内工具]
  tools -->|USE_MCP=true| mcp[MCP stdio]
  local --> store[(知识库与 tickets.db)]
  mcp --> store
  nodes[各节点] --> spans[(trace_spans)]
  spans --> traces[GET /traces]
```

审核台在人工通过之前停住。下面这张退款单已经检索并引用 `kb-billing-refund`，状态要等通过或驳回才会写。

![审核台：退款单等待人工审核](docs/images/review-desk.png)

```text
START → ingest → classify → act ⇄ (最多 4 步，失败可重试) → draft → hitl_gate
                                                                 ├─ awaiting_human（写入 SQLite 后暂停）
                                                                 └─ finalize → END

act 只可选 search_knowledge_base、get_ticket_history，或 finish。
update_ticket_status 只能由 finalize 在人审之后调用。

act / finalize 工具面：
  未设置 RAG_BASE_URL，或设为 local、USE_MCP=false → 进程内关键词工具
  RAG_BASE_URL=http://127.0.0.1:8001 → 知识库检索改调 citation-rag-agent
  USE_MCP=true → MCP stdio 客户端 → python -m mcp_server
```

| 节点 | 职责 |
|------|------|
| `ingest` | 写入共享 SQLite 工单库 |
| `classify` | 优先级 + 类别（有 Key 时由模型判断，失败退回关键词） |
| `act` | 在白名单里选择检索或查历史；`other` 的 stub 不检索；失败可软降级重试 |
| `draft` | 回复草稿 + 引用 |
| `hitl_gate` | 除非 `auto_approve`，否则暂停等人审 |
| `finalize` | 结案或升级（经网关写状态） |

## 快速开始

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
# 显式运行需要拉起 MCP stdio 子进程的集成测试：
pytest -m integration
uvicorn ticket_triage.api.main:app --reload --port 8000
```

发起一次运行。接口立刻返回 202 和 `trace_id`，分类和起草在后台进行：

```bash
curl -X POST http://127.0.0.1:8000/runs -H "Content-Type: application/json" -d "{\"ticket\":{\"ticket_id\":\"t-1\",\"subject\":\"Urgent refund\",\"body\":\"Charged twice\"},\"auto_approve\":false}"
curl http://127.0.0.1:8000/runs/<trace_id>
```

人工通过 / 驳回。先设置 `REVIEW_API_KEY`，请求带上同一把密钥和审核人：

```bash
curl -X POST http://127.0.0.1:8000/runs/human -H "Content-Type: application/json" -H "X-API-Key: change-me" -d "{\"ticket_id\":\"t-1\",\"approved\":true,\"reviewer\":\"ada\"}"
```

停进程后再恢复的完整演示：`powershell -File .\scripts\demo_hitl.ps1`，记录见 [docs/DEMO.md](docs/DEMO.md)。

审核台：启动 API 后打开 http://127.0.0.1:8000/review 。页面先填写 `REVIEW_API_KEY` 和审核人，才能看草稿、通过、驳回或重新送审。审核人会写进状态历史。已处理工单列在左侧，可以重新送审，原来的结案或升级记录会留在历史里。

服务只跑一个进程。`uvicorn` 使用 `--workers 1`，启动时会锁住数据目录里的 `instance.lock`。第二个进程拿不到这把锁，会直接退出。待审状态仍在 SQLite，不要用多个 worker 或两个容器写同一份库。

`POST /runs` 不在请求里等模型。返回 202 之后，最多 `RUN_WORKERS` 个线程同时做分类、选工具和起草。审核台和 `GET /runs/{trace_id}` 轮询到 `awaiting_human` 或 `failed`。同一张单还在跑时，再次提交返回同一个 `trace_id`。进程重启后，没跑完的运行变成 `failed` / `run_interrupted`。人工通过和驳回仍然当场完成。

### 用真实模型分类和起草

默认是 stub，测试和 CI 不需要 Key。设置后，类别、优先级和草稿都由模型产生：

```powershell
$env:LLM_PROVIDER="anthropic"   # 或 openai；本机 .env 已指向 DeepSeek 时可直接跑
$env:ANTHROPIC_API_KEY="..."    # openai 则设 OPENAI_API_KEY，可选 OPENAI_BASE_URL
python evals/run_evals.py
```

不设模型时，同一条命令走 stub，这就是 CI 的门禁。评测会忽略 `RAG_BASE_URL`，固定用仓库里的 3 篇关键词文档。

配了 Langfuse 时，每次运行会多送一条 `ticket-triage` trace。版本号（例如 `openai-act-v3` 或 `stub-act-v1`）写在 trace 的 version 和 tag 上，延迟记成 `latency_ms`，有没有退回 stub 记成 `no_model_fallback`。黄金集还会把类别、优先级、引用和工具选择的得分贴到同一条 trace 上。不配 Key 时只保留原来的 SQLite 节点追踪。

```powershell
$env:LANGFUSE_PUBLIC_KEY="pk-lf-..."
$env:LANGFUSE_SECRET_KEY="sk-lf-..."
$env:LANGFUSE_BASE_URL="https://cloud.langfuse.com"   # 美国区用 https://us.cloud.langfuse.com
```

分类只能落在现有的四个类别和四个优先级里。不合规、超时或不是 JSON 时，退回关键词结果并记 `classify_model_failed`。起草时提示里只给本次检索到的文档。模型返回的引用编号不在其中时会被删掉；没有检索结果时引用为空。模型这条路径上，任何一张工单的分类、工具调度或草稿退回 stub，评测都会失败；延迟只记录，不卡 2 秒预算。约 40 条黄金集还要求非法工具调用为 0。stub 的工具选择必须全对；模型路径的工具选择准确率门槛是 0.70。

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
- 写操作 `status` 白名单：`open|resolved|escalated|pending`，并打审计日志。人审和重新送审会把审核人记进历史
- 待审列表、追踪、`POST /runs/human` 和重新送审需要请求头 `X-API-Key`，值是 `REVIEW_API_KEY`。没配置密钥时这些接口返回 401
- 工单编号最长 64，只允许字母、数字、`.`、`_`、`-`。主题最长 200，正文最长 4000。请求体超过 64KB 返回 413
- MCP 调用默认 5 秒超时；超时记 `enrich_failed` 后软降级起草
- `GET /health` 检查运行库和工单库是否可写，并确认本进程持有单进程锁。设置了 `RAG_BASE_URL` 时还会请求问答服务的 `/health`；失败返回 503
- Docker 镜像以 uid 10001 的 `appuser` 运行，并带 HEALTHCHECK。挂载数据卷时，目录需要对这个用户可写。镜像里包含 `mcp_server`，`USE_MCP=true` 时可以在同一镜像里拉起工具进程

## 失败模式（面试可讲）

| 失败 | 行为 |
|------|------|
| 知识库 / 工具报错 / MCP 超时 / 问答服务连不上 | 最多重试 `max_tool_retries` 次，然后无引用起草 |
| 模型分类报错、不是 JSON，或类别/优先级不在名单里 | 退回关键词分类并记 `classify_model_failed`，流程继续；评测里视为不通过 |
| 模型起草报错或输出不是 JSON | 退回 stub 草稿并记 `draft_model_failed`，仍进人审；评测里视为不通过 |
| 模型写了检索结果以外的文档编号 | 该编号从引用里删掉 |
| 检索阶段要求改状态，或读取其他工单 | 不执行，记 `tool_not_allowed` 或 `ticket_id_mismatch`，流程继续 |
| 工具调度不是 JSON | 改用固定规则选工具并记 `act_model_failed`；评测里视为不通过 |
| 模型接口传输中断 | 同一请求最多再试 2 次；仍失败则按上面的模型失败处理 |
| 上下文超过 `CONTEXT_TOKEN_BUDGET` | 先删最早的成功工具结果，再删上一步错误码；工单和本次引用留到最后 |
| 低置信度草稿 | 默认仍进人审；人可改稿 |
| 人审驳回 | 状态为 `escalated`，不会静默丢弃 |
| 非法 status | MCP/处理器返回 `status_not_allowed` |
| 无待审运行 | HTTP 404，`no_pending_run` |
| 工单不存在或尚未结案就重新送审 | HTTP 404 `ticket_not_found`，或 HTTP 409 `not_closed` / `already_pending` |
| 同一张单再次提交 | 还在后台跑就返回同一个 `trace_id`；已经待审就返回原来的草稿，不再跑一遍；已结案或已升级则 HTTP 409 `already_closed`，历史保持原样 |
| 审核密钥缺失或错误 | HTTP 401，`review_auth_not_configured` 或 `review_auth_failed` |
| 后台运行失败 | `GET /runs/{trace_id}` 的 status 为 `failed`，`errors` 为 `internal_error`，带同一个 `trace_id`，不返回异常原文 |
| 进程在运行中退出 | 下次启动把该次运行标成 `failed` / `run_interrupted` |
| 人审写状态失败 | HTTP 500，`internal_error`，响应里带 `trace_id` |

## 简历话术模板

> 用工单分流 Agent 做白名单工具调用：模型自行决定是否检索知识库和工单历史，写状态必须经人工审核。40 条黄金集进入 CI，非法工具调用为 0，类别准确率 1.0，优先级准确率 1.0，引用精确率 1.0，不该引用时伪造率为 0。DeepSeek（deepseek-flash，`openai-act-v3`）上工具选择准确率 1.0，知识库没有的问题不再检索，也没有编造引用。

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
2. **P2** — MCP 工具服务 + `USE_MCP`（已完成，见 `docs/MCP.md`）
3. **P3** — 黄金 JSONL + CI 门禁（已完成，含不得伪造引用）
4. **P4** — 带引用的 RAG 在并列仓库 `citation-rag-agent`（已完成）
5. **P5** — 人审状态落 SQLite，重启可恢复；`GET /traces/{trace_id}` 看节点耗时（已完成，见 `docs/P5_RUNBOOK.md`）
6. **P6** — 设置 `RAG_BASE_URL` 后，知识库检索改调 citation-rag-agent（已完成，见 `docs/P6.md`）
7. **P7** — 分类之后在白名单里自选工具，40 条黄金集卡住非法调用（已完成，见 `docs/P7_AGENT_PLAN.md`）
