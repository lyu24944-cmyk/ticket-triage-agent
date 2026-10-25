# Codex 交接说明（工单分流 Agent）

把这份文档连同仓库当前代码一起交给下一个代理。目标是在**不破坏现有门禁与演示路径**的前提下继续完善。

## 1. 仓库与分支

| 项 | 值 |
|---|---|
| 本仓库 | `C:\Users\Administrator\Projects\ticket-triage-agent` |
| 远程 | https://github.com/lyu24944-cmyk/ticket-triage-agent |
| 分支 | `master` |
| 并列仓库 | `C:\Users\Administrator\Projects\citation-rag-agent`（RAG，默认分支 `cursor/p4-citation-rag`） |
| 远程 RAG | https://github.com/lyu24944-cmyk/citation-rag-agent |

两个仓库保持分离：工单通过 HTTP `RAG_BASE_URL` 调问答服务，不要合并成单体。

本文件描述已提交的代码。流程是 `act`，审核接口要密钥。不要按旧的固定 `enrich` 流水线或「无鉴权」假设改。

## 2. 工作区

相对已提交代码，不要把下面两项放进功能提交：

- `.env`（含 API Key，已在 `.gitignore`）
- `.data/`（`tickets.db`、`runtime.db`、`instance.lock`、模型缓存）
- `docs/面试演讲稿.txt`（个人讲稿，除非用户明确要求）

## 3. 产品定位

作品集 Demo，运行时已收成单进程服务，还不是多副本生产系统：

- 知识库只有 3 篇政策，没有 Zendesk
- 流程：`ingest → classify → act ⇄（最多 4 步）→ draft → hitl_gate → finalize`
- `act` 只能选 `search_knowledge_base`、`get_ticket_history` 或 `finish`。`update_ticket_status` 只在人审之后由 `finalize` 调用
- 默认 CI / pytest 用 stub，不调用外网模型
- 本地 `.env` 可切 DeepSeek（OpenAI 兼容）做分类、选工具和起草。提示词版本是 `openai-act-v3`（stub 为 `stub-act-v1`）

## 4. 已完成能力（P1–P7）

| 能力 | 状态 | 关键代码 |
|---|---|---|
| LangGraph 工单分流 + HITL | 完成 | `graph/workflow.py`, `graph/nodes.py` |
| 白名单内自选工具 | 完成 | `graph/nodes.py` `act_node`，`llm/actions.py` |
| MCP 工具面 | 完成 | `mcp_server/`, `tools/mcp_gateway.py` |
| 黄金评测 + CI（约 40 条） | 完成 | `evals/`, `.github/workflows/evals.yml` |
| 可恢复人审 + 节点追踪 | 完成 | `runtime.py`（`pending_runs`, `trace_spans`） |
| 调用 citation-rag | 完成 | `tools/rag_client.py`，`RAG_BASE_URL` |
| 真实模型分类、调度、起草 | 完成 | `llm/model.py`；失败记 `classify_model_failed` / `act_model_failed` / `draft_model_failed` |
| 审核台 | 完成 | `GET /review` → `api/review.html` |
| 待审、已处理、重新送审 | 完成 | `GET /runs/pending`，`GET /tickets`，`POST /tickets/{id}/reopen` |
| 审核接口密钥 | 完成 | `REVIEW_API_KEY`；见下方鉴权范围 |
| 受理后后台运行 | 完成 | `POST /runs` 返回 202；`run_jobs`；`RUN_WORKERS` |
| 单进程锁与就绪检查 | 完成 | `process_lock.py`，`GET /health` |
| 上下文预算 | 完成 | `llm/context.py`，`CONTEXT_TOKEN_BUDGET` |
| Langfuse（可选） | 完成 | `observability.py` |

### 鉴权范围

要请求头 `X-API-Key`（值等于 `REVIEW_API_KEY`）的接口：`GET /runs/pending`、`GET /traces/{trace_id}`、`POST /runs/human`、`GET /tickets`、`POST /tickets/{id}/reopen`。没配置密钥时这些接口返回 401。

`POST /runs` 和 `GET /runs/{trace_id}` 目前没有这把密钥。`trace_id` 是查询这一次运行的凭证。`RunRequest.auto_approve` 仍由调用方传入；`REQUIRE_HITL=false` 时人审关卡会直接通过。

### 审核台行为

- 地址：`http://127.0.0.1:8000/review`
- 页面先填 `REVIEW_API_KEY` 和审核人
- 左侧：待审 / 已处理 / 新建
- 待审：草稿、引用原文、本次工具调用、通过 / 驳回 / 修改后通过、节点耗时
- 已处理：状态历史；「重新送审」写回 `pending`，历史追加 `pending`，不覆盖 `escalated`/`resolved`
- 页面只调 API，无业务逻辑

### 模型路径约定

- Provider：`LLM_PROVIDER=openai` + `OPENAI_BASE_URL` + `OPENAI_API_KEY`（或 anthropic）
- 分类：只允许 `billing|technical|account|other` × `low|medium|high|critical`
- 起草：citations 只能来自本次 `kb_hits`；空检索 → 空 citations
- 失败：分类、调度、起草退回 stub，并分别记 `classify_model_failed`、`act_model_failed`、`draft_model_failed`
- 评测：模型路径上任一 fallback → 门禁失败；p95 延迟只记录、不卡 2 秒。stub 路径不因这些字符串失败
- 工具异常的错误码仍叫 `enrich_failed`，图状态仍可能是 `enrich_error`。追踪里的节点名是 `act`，不要再去找 `enrich` 节点

## 5. 本地如何跑

```powershell
# 工单仓库
cd C:\Users\Administrator\Projects\ticket-triage-agent
.\.venv\Scripts\activate
pytest
# 显式运行需要拉起 MCP stdio 子进程的集成测试：
pytest -m integration
# 可选：有 .env 时跑模型评测
python evals/run_evals.py

# 工单 API（8000）。只开一个进程；第二个进程会因 instance.lock 退出
uvicorn ticket_triage.api.main:app --host 127.0.0.1 --port 8000 --workers 1
# 浏览器：http://127.0.0.1:8000/review

# 并列 RAG（8001，可选；不设 RAG_BASE_URL 则用关键词 KB）
cd C:\Users\Administrator\Projects\citation-rag-agent
uvicorn citation_rag.api.main:app --host 127.0.0.1 --port 8001
```

测试夹具：`tests/conftest.py` 强制 `llm_provider=stub`、`rag_base_url=None`、隔离 SQLite。

## 6. 数据落盘

| 文件 | 用途 |
|---|---|
| `.data/tickets.db` | 工单状态与 history |
| `.data/runtime.db` | `pending_runs`、`closed_runs`、`run_jobs`、`trace_spans` |
| `.data/instance.lock` | 单进程锁。持有者退出前不要对同一目录再起一个 API |
| `.env` | 本地 Key（勿提交、勿打印） |

合法 status：`open | resolved | escalated | pending`。

`POST /runs` 把 `running` 写入 `run_jobs` 后立刻返回。分类和起草在最多 `RUN_WORKERS` 个线程里进行。进程在运行中退出时，下次启动把该次运行标成 `failed` / `run_interrupted`。已经停在 `awaiting_human` 的单仍可审核。

## 7. 建议下一步（按价值）

以下为**建议**，请先与用户确认再开大改。P1–P7 和当前这批运行时收口已经在仓库里，不要重做工具自选、审核台或 Langfuse 接入。

1. **调用方身份与人审策略**：给 `POST /runs` 和 `GET /runs/{trace_id}` 单独的服务身份。是否跳过人审由服务端决定，不要继续信任请求里的 `auto_approve`。
2. **可恢复的任务**：进行中的运行现在只活在本进程线程池里。若保持单实例，写明并发上限、备份和恢复；若要多副本，换掉 SQLite 文件锁，让没跑完的任务能被别的进程领走。
3. **值班信号**：结构化日志、队列深度、模型退回和工具失败的指标。CI 目前是 stub 的 `pytest` 加黄金评测，默认跳过 MCP 集成测试。
4. **客户数据**：正文进 SQLite 和模型。补保留期限、送出模型前的脱敏，以及和受理密钥分开的审核人身份。
5. **提示词可回滚**：版本号写在代码里（`openai-act-v3`）。退回比例或工具选择掉下去时要能切回上一版。

**不要默认做的事**

- 不要把 API Key 写进仓库或评测报告
- 不要杀掉用户自己开着的 `citation-rag`（8001）除非明确告知
- 不要改坏 stub CI 路径
- 不要把两个仓库硬合并
- 不要在未要求时 `git push`
- 不要对同一个数据目录开多个 worker

## 8. 关键入口文件

```
ticket-triage-agent/
  src/ticket_triage/
    api/main.py          # HTTP：/health /review /runs /runs/pending /runs/human /tickets /reopen /traces
    api/review.html      # 审核台
    health.py            # 就绪检查
    process_lock.py      # instance.lock
    graph/workflow.py    # 图编译、后台受理、run/resume/reopen
    graph/nodes.py       # ingest / classify / act / draft / hitl_gate / finalize
    llm/model.py         # 模型 classify + next_action + draft
    llm/context.py       # 上下文预算
    llm/stub.py          # 关键词与固定工具顺序
    tools/gateway.py     # MCP / RAG / 本地切换
    tools/rag_client.py  # 调 citation-rag
    tools/tickets.py     # tickets.db
    runtime.py           # pending / closed / run_jobs / traces
    observability.py     # 可选 Langfuse
  evals/run_evals.py
  docs/P5_RUNBOOK.md     # 重启后人审和节点追踪
```

## 9. 给 Codex 的起始指令模板

可直接粘贴：

```text
请阅读 docs/CODEX_HANDOFF.md 和 docs/P5_RUNBOOK.md，在已提交代码上继续。

硬约束：
- 默认 pytest/CI 保持 stub，不依赖 API Key
- 不提交 .env；不打印密钥
- 两个仓库保持分离；工单通过 RAG_BASE_URL 调 citation-rag
- 引用只能来自本次检索；分类标签必须在白名单内；模型 fallback 必须让评测失败
- 图上的工具节点是 act。enrich_failed 只是工具异常的错误码
- 审核接口要 REVIEW_API_KEY。同一数据目录只运行一个 API 进程

先确认用户要做交接说明第 7 节的哪一项，再改代码。
```

## 10. 已知边角

- 留档功能上线前已驳回的工单：`closed_runs` 无草稿，重新送审会用占位文案「上次草稿未留档…」
- `RAG_BASE_URL` 默认不在 `.env`；不设则走关键词 KB。评测会忽略该变量
- Docker 以 uid 10001 的 `appuser` 运行。挂载数据卷时目录要对这个用户可写
- Windows：PowerShell 脚本注意 UTF-8 BOM；不要用代理误判 502 当服务在线
- 聊天里出现过的 API Key 不要回显；若已粘贴过，建议用户自行轮换
