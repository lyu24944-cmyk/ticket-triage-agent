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

- 知识库只有 3 篇政策；进线可用模拟 Zendesk/邮件 Webhook（`/v1/webhooks/*`），非真实 OAuth
- 流程：`ingest → classify → act ⇄（最多 4 步）→ draft → hitl_gate → finalize`
- `act` 只能选 `search_knowledge_base`、`get_ticket_history` 或 `finish`。`update_ticket_status` 只在人审之后由 `finalize` 调用
- 默认 CI / pytest 用 stub，不调用外网模型
- 本地 `.env` 可切 DeepSeek（OpenAI 兼容）做分类、选工具和起草。默认 `PROMPT_PIN=current` → `openai-act-v3`；`previous` → `openai-act-v2`（stub 为 `stub-act-v1`）

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
| 审核台 | 完成 | Vite+React `web/` → `api/static/review`；`GET /review`；只打 `/v1` |
| 待审、已处理、重新送审 | 完成 | `GET /runs/pending`，`GET /tickets`，`POST /tickets/{id}/reopen` |
| 受理密钥与审核密钥 | 完成 | `SERVICE_API_KEY`、`REVIEW_API_KEY`；`*_FILE` / `*_PREVIOUS` 见 `docs/SECRETS.md` |
| 网关 TLS / 限流 / body | 完成 | `deploy/nginx` + `docs/GATEWAY.md`；API 不对外 publish |
| 依赖/镜像扫描与非 root | 完成 | `.github/workflows/security.yml`；`docs/SECURITY_CI.md`；uid 10001 |
| 冷备份 / RPO·RTO | 完成 | `scripts/backup_sqlite.py`；`docs/BACKUP.md`（RPO≤24h，RTO≤1h） |
| 值班日志与告警 | 完成 | ops 固定键含 `alert_code`；Webhook/SMTP；`docs/ONCALL.md` |
| 人员 JWT / 审计 / DSAR | 完成 | `POST /auth/review/token`；`audit_events`；`/privacy/*`；红队门禁 |
| 集成 P3：Webhook /v1 / 轻量租户 | 完成 | `docs/P3_INTEGRATION.md`；`/v1/webhooks/*`；`TENANTS_JSON`；结案回调 |
| 受理后可恢复的任务 | 完成 | `POST /runs` 返回 202；`run_jobs` 检查点；`RUN_WORKERS`；`RUN_QUEUE_LIMIT` |
| 单进程锁与就绪检查 | 完成 | `process_lock.py`，`GET /health` |
| 上下文预算 | 完成 | `llm/context.py`，`CONTEXT_TOKEN_BUDGET` |
| Langfuse（可选） | 完成 | `observability.py` |

### 鉴权范围

两把密钥都放在请求头 `X-API-Key`，值不能混用。

- 受理：`POST /runs`、`GET /runs/{trace_id}` 的值是 `SERVICE_API_KEY`。没配置时返回 `service_auth_not_configured`。
- 审核：`GET /runs/pending`、`GET /traces/{trace_id}`、`POST /runs/human`、`GET /tickets`、`POST /tickets/{id}/reopen` 的值是 `REVIEW_API_KEY`。没配置时返回 `review_auth_not_configured`。

`trace_id` 不是读草稿的凭证。是否跳过人审只看 `REQUIRE_HITL`。请求体里的 `auto_approve` 会被忽略。

### 审核台行为

- 地址：`http://127.0.0.1:8000/review`
- 页面先填 `REVIEW_API_KEY` 和审核人。新建工单还要填 `SERVICE_API_KEY`
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

容量按单实例锁定，见 `docs/DECISIONS.md`。不要一边持有 `instance.lock`，一边用多个副本写同一份 SQLite。

`POST /runs` 把请求和 `running` 写入 `run_jobs` 后立刻返回。最多 `RUN_WORKERS`（默认 2）张单同时执行，按 `created_at`（含毫秒）与入库 `rowid` 领取，同一秒内也保持受理顺序。已受理未结束的单超过 `RUN_QUEUE_LIMIT`（默认 32）时返回 429 `queue_full`。每完成一个节点就写下 `checkpoint_json` 和 `next_node`。进程退出后，下次启动从 `next_node` 继续；正在执行的那个节点会再跑一次，已经完成的不重跑。没有 `request_json` 的旧行仍标成 `failed` / `run_interrupted`。已经停在 `awaiting_human` 的单仍可审核。表结构变更记在 `schema_migrations`（当前版本 2），启动时给旧库补列。

## 7. 建议下一步（按价值）

以下为**建议**，请先与用户确认再开大改。P1–P7 和当前这批运行时收口已经在仓库里，不要重做工具自选、审核台或 Langfuse 接入。

1. **可恢复的任务**：已做，并且选定单实例。任务在 `run_jobs`，重启后从最后完成的节点继续。并发、队列和备份见 `docs/P5_RUNBOOK.md`。
2. **值班信号**：已做一页纸说明 `docs/ONCALL.md`。日志固定键含 `alert_code` / `model_failed`（`ticket_triage.ops`）；`OPS_LOG_JSON` 进集中日志；`ALERT_WEBHOOK_URL` / SMTP 外发。`GET /health` 已有 `checks.queue`。不上完整 APM。CI 仍是 stub pytest + 黄金评测，不把这些日志当合并门槛。
3. **客户数据**：已做。保留期限与可进提示词字段见 `docs/DATA.md`；送模型前脱敏在 `privacy.py`；审核人只在审核接口，不与 `SERVICE_API_KEY` 混用。
4. **提示词可回滚**：已做。`PROMPT_PIN=current|previous`（v3/v2），见 `docs/PROMPTS.md`。本地可用 `evals/compare_prompt_versions.py` 对比；内测影子评测 `evals/run_shadow.py`（抽样真实单只打分不写状态，退回率信号可回滚）。CI 只跑 stub + 当前销钉，不打两次外网模型。
5. **审核 OIDC**：设 `REVIEW_OIDC_ISSUER` 后 mint 关闭，人审只接受 IdP Bearer（JWKS/RS256）；`sub` 口径不变。见 `docs/P1_IDENTITY.md`。

**上面各项（加同秒排队、工人不挂死、备份/杀进程演练、OIDC、影子评测）都做完了。** 部署继续 `uvicorn` 一个进程、一份数据目录。只有**确实需要同时跑两个写进程**时，才另开一题：工单库和运行库换到 Postgres，用租约代替 `instance.lock`，进行中的任务能被另一个进程领走。在那之前不要改成多副本。

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
    api/static/review/   # 审核台构建产物（源码 web/）
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
  docs/ONCALL.md         # 值班：queue_full / *_model_failed / enrich_failed 先看哪里
  docs/DATA.md           # 正文保留期限、脱敏、可进提示词字段、审核人身份
  docs/PROMPTS.md        # PROMPT_PIN 回滚与对比脚本
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
- 受理接口要 SERVICE_API_KEY，审核接口要 REVIEW_API_KEY，两把不能混用
- 同一数据目录只运行一个 API 进程

先确认用户要做交接说明第 7 节的哪一项，再改代码。
```

## 10. 已知边角

- 留档功能上线前已驳回的工单：`closed_runs` 无草稿，重新送审会用占位文案「上次草稿未留档…」
- `RAG_BASE_URL` 默认不在 `.env`；不设则走关键词 KB。评测会忽略该变量
- Docker 以 uid 10001 的 `appuser` 运行。挂载数据卷时目录要对这个用户可写
- Windows：PowerShell 脚本注意 UTF-8 BOM；不要用代理误判 502 当服务在线
- 聊天里出现过的 API Key 不要回显；若已粘贴过，建议用户自行轮换
- `evals/run_evals.py` 与 `scripts/demo_p6.py` 直接调 `run_triage`，一次跑完；杀进程不会从节点中间恢复。只有 `POST /runs`（`submit_triage` → `run_jobs`）才按检查点续跑
- 写检查点或保存最终结果抛错：放下领取标记让别的工人接着跑，不把单挂在领取上；工人线程退出会补员到 `RUN_WORKERS`
