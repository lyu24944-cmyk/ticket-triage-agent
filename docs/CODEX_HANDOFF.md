# Codex 交接说明（工单分流 Agent）

把这份文档连同仓库当前工作区一起交给 Codex。目标是在**不破坏现有门禁与演示路径**的前提下继续完善。

## 1. 仓库与分支

| 项 | 值 |
|---|---|
| 本仓库 | `C:\Users\Administrator\Projects\ticket-triage-agent` |
| 远程 | https://github.com/lyu24944-cmyk/ticket-triage-agent |
| 分支 | `master`（已推到 `7d07186`） |
| 并列仓库 | `C:\Users\Administrator\Projects\citation-rag-agent`（RAG，默认分支 `cursor/p4-citation-rag`） |
| 远程 RAG | https://github.com/lyu24944-cmyk/citation-rag-agent |

两个仓库保持分离：工单通过 HTTP `RAG_BASE_URL` 调问答服务，不要合并成单体。

## 2. 当前工作区状态（重要）

相对 `origin/master`，有一批**尚未提交**的本地改动。Codex 接手后应先确认这些文件都在，再决定提交或继续改：

**已修改**

- `README.md`
- `evals/run_evals.py`
- `pyproject.toml`（含 `ticket_triage = ["api/*.html"]`）
- `src/ticket_triage/api/main.py`
- `src/ticket_triage/config.py`
- `src/ticket_triage/graph/nodes.py`
- `src/ticket_triage/graph/workflow.py`
- `src/ticket_triage/llm/model.py`
- `src/ticket_triage/llm/stub.py`
- `src/ticket_triage/runtime.py`
- `src/ticket_triage/tools/gateway.py`
- `src/ticket_triage/tools/tickets.py`
- `tests/test_model_llm.py`

**未跟踪（新增）**

- `src/ticket_triage/api/review.html`
- `tests/test_review.py`

**不要提交**

- `.env`（含 API Key，已在 `.gitignore`）
- `.data/`（`tickets.db`、`runtime.db`、模型缓存等）

## 3. 产品定位

作品集 Demo，不是生产系统：

- 无鉴权、无 Zendesk、知识库只有 3 篇政策
- 流程固定：`ingest → classify → enrich → draft → hitl_gate → finalize`
- 默认 CI / pytest 用 stub，不调用外网模型
- 本地 `.env` 可切 DeepSeek（OpenAI 兼容）做分类 + 起草

## 4. 已完成能力（P1–P6 + 近期增量）

| 能力 | 状态 | 关键代码 |
|---|---|---|
| LangGraph 工单分流 + HITL | 完成 | `graph/workflow.py`, `graph/nodes.py` |
| MCP 工具面 | 完成 | `mcp_server/`, `tools/mcp_gateway.py` |
| 黄金评测 + CI | 完成 | `evals/`, `.github/workflows/evals.yml` |
| 可恢复人审 + 节点追踪 | 完成 | `runtime.py`（`pending_runs`, `trace_spans`） |
| 调用 citation-rag | 完成 | `tools/rag_client.py`，`RAG_BASE_URL` |
| 真实模型起草 + 引用过滤 | 完成 | `llm/model.py` `draft_reply` |
| **真实模型分类**（未提交） | 完成 | `llm/model.py` `classify`；失败记 `classify_model_failed` |
| **审核台 UI**（未提交） | 完成 | `GET /review` → `api/review.html` |
| **待审列表**（未提交） | 完成 | `GET /runs/pending` |
| **已处理列表 + 重新送审**（未提交） | 完成 | `GET /tickets`，`POST /tickets/{id}/reopen`；结案草稿进 `closed_runs` |

### 审核台行为

- 地址：`http://127.0.0.1:8000/review`
- 左侧：待审 / 已处理 / 新建
- 待审：草稿、引用原文、通过 / 驳回 / 修改后通过、节点耗时
- 已处理：状态历史；「重新送审」写回 `pending`，历史追加 `pending`，不覆盖 `escalated`/`resolved`
- 页面只调 API，无业务逻辑

### 模型路径约定

- Provider：`LLM_PROVIDER=openai` + `OPENAI_BASE_URL` + `OPENAI_API_KEY`（或 anthropic）
- 分类：只允许 `billing|technical|account|other` × `low|medium|high|critical`
- 起草：citations 只能来自本次 `kb_hits`；空检索 → 空 citations
- 失败：分类退回 stub 并记 `classify_model_failed`；起草退回 stub 并记 `draft_model_failed`
- 评测：模型路径上任一 fallback → 门禁失败；p95 延迟只记录、不卡 2s（stub 仍卡）

## 5. 本地如何跑

```powershell
# 工单仓库
cd C:\Users\Administrator\Projects\ticket-triage-agent
.\.venv\Scripts\activate
pytest
# 可选：有 .env 时跑模型评测
python evals/run_evals.py

# 工单 API（8000）
uvicorn ticket_triage.api.main:app --host 127.0.0.1 --port 8000
# 浏览器：http://127.0.0.1:8000/review

# 并列 RAG（8001，可选；不设 RAG_BASE_URL 则用关键词 KB）
cd C:\Users\Administrator\Projects\citation-rag-agent
uvicorn citation_rag.api.main:app --host 127.0.0.1 --port 8001
```

测试夹具：`tests/conftest.py` 强制 `llm_provider=stub`、`rag_base_url=None`、隔离 SQLite。

最近一次本地验证（未提交改动之上）：

- `pytest`：36 passed
- `python evals/run_evals.py`（DeepSeek）：门禁通过，category/priority 1.0，prompt_version=`openai-classify-draft-v1`

## 6. 数据落盘

| 文件 | 用途 |
|---|---|
| `.data/tickets.db` | 工单状态与 history |
| `.data/runtime.db` | `pending_runs`、`closed_runs`、`trace_spans` |
| `.env` | 本地 Key（勿提交、勿打印） |

合法 status：`open | resolved | escalated | pending`。

## 7. 建议 Codex 优先完善的方向（按价值）

以下为**建议**，请先与用户确认再开大改：

1. **提交当前未提交改动**（审核台 + 模型分类），中文一句话 commit message，说明「为什么」。
2. **审核台体验**：已处理列表筛选/分页；重新送审后高亮；展示 `classify_model_failed` / `draft_model_failed`；旧工单无 `closed_runs` 时的草稿提示更清晰。
3. **可观测性**：接 Langfuse / LangSmith（config 已有 `langfuse_*` 占位），按 prompt_version 看质量与延迟。
4. **评测加深**：扩大黄金集；可选「模型路径」CI job（需密钥 secret，默认 job 仍 stub）。
5. **Agent 下一步跃迁（大）**：在允许的工具集合里让模型选择调用，而不是整条写死流水线；仍要保留工具白名单与评测门禁。
6. **生产向缺口（Demo 可不做）**：鉴权、幂等、队列、提示词注入防护、隐私脱敏。

**不要默认做的事**

- 不要把 API Key 写进仓库或评测报告
- 不要杀掉用户自己开着的 `citation-rag`（8001）除非明确告知
- 不要改坏 stub CI 路径
- 不要把两个仓库硬合并
- 不要在未要求时 `git push`

## 8. 关键入口文件

```
ticket-triage-agent/
  src/ticket_triage/
    api/main.py          # HTTP：/health /review /runs /runs/pending /runs/human /tickets /reopen /traces
    api/review.html      # 审核台
    graph/workflow.py    # 图编译、run/resume/reopen
    graph/nodes.py       # 各节点；分类/起草失败降级
    llm/model.py         # 模型 classify + draft
    llm/stub.py          # 关键词；get_llm()
    tools/gateway.py     # MCP / RAG / 本地切换
    tools/rag_client.py  # 调 citation-rag
    tools/tickets.py     # tickets.db
    runtime.py           # pending / closed / traces
  evals/run_evals.py
  tests/test_review.py
  tests/test_model_llm.py
```

## 9. 给 Codex 的起始指令模板

可直接粘贴：

```text
请阅读 docs/CODEX_HANDOFF.md，在 ticket-triage-agent 当前未提交改动之上继续完善。

硬约束：
- 默认 pytest/CI 保持 stub，不依赖 API Key
- 不提交 .env；不打印密钥
- 两个仓库保持分离；工单通过 RAG_BASE_URL 调 citation-rag
- 引用只能来自本次检索；分类标签必须在白名单内；模型 fallback 必须让评测失败

建议第一步：把「审核台 + 已处理/重新送审 + 模型分类」整理干净并本地 pytest 全绿，再问我是否提交。

完善优先级：审核台体验 → 可观测性 → 评测加深；不要一上来做工具自主选择，除非我明确要求。
```

## 10. 已知边角

- 留档功能上线前已驳回的工单：`closed_runs` 无草稿，重新送审会用占位文案「上次草稿未留档…」
- `RAG_BASE_URL` 默认不在 `.env`；不设则走关键词 KB
- Windows：PowerShell 脚本注意 UTF-8 BOM；不要用代理误判 502 当服务在线
- 用户曾在聊天中粘贴过 DeepSeek Key；切勿回显；建议用户自行轮换
