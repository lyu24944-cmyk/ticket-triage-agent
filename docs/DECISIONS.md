# 项目决策（已锁定）

## P1 业务域

**工单分流 Agent（Ticket Triage Agent）**  
流程：入站客服工单 → 分类 → act（在白名单里选择工具）→ 起草回复 → 人工审核通过 → 结案或升级。

在未指定个人背景时采用该默认域的原因：
- 规划/执行图清晰（不是聊天机器人）
- 天然适合 HITL 关卡（通过草稿 / 升级）
- 容易做黄金集评测（优先级、类别、必须引用策略）
- 后续可无缝对齐 P6「客服/工单」方向

## 锁定技术栈

| 层级 | 选型 |
|------|------|
| 语言 | Python 3.11+ |
| 编排 | LangGraph（显式状态） |
| API | FastAPI + Pydantic |
| 工具（P1） | 进程内类型化工具（模拟 KB + 工单库） |
| 工具（P2） | MCP Server（stdio）+ `USE_MCP` 网关；工单用共享 SQLite |
| 可观测性 | Langfuse（可选，环境变量开启）；始终有结构化日志 |
| 评测（P3） | `evals/` 黄金 JSONL + 确定性打分 + 可选 LLM-as-judge |
| 部署 | Docker + GitHub Actions CI |

## 运行容量（已锁定）

**单实例。** 部署保持 `uvicorn` 一个进程、一份数据目录。不把工单库和运行库换到 Postgres，不用租约，也不按多副本部署。

- 一个数据目录只跑一个 API 进程，`instance.lock` 挡住第二个进程
- 同时执行的单最多 `RUN_WORKERS`（默认 2）
- 已受理未结束的单最多 `RUN_QUEUE_LIMIT`（默认 32），超出的新工单直接拒绝
- 任务写在 SQLite。进程退出后从最后完成的节点继续，不靠进程里的线程活着
- 生产入口是 TLS 网关（`deploy/nginx`）；API 只在内网。速率与 body 上限在网关统一，见 `docs/GATEWAY.md`。加网关不等于加 API 副本
- 镜像以 uid 10001 的 `appuser` 运行；依赖与镜像 CRITICAL/HIGH 扫描进 `security.yml`，见 `docs/SECURITY_CI.md`
- 数据：每日冷备份 SQLite（WAL 收口）；目标 RPO≤24h、RTO≤1h；季度按 P5 演练，见 `docs/BACKUP.md`
- 值班：ops 固定键进集中日志（可选 JSON）；`queue_full` / `*_model_failed` / `enrich_failed` 走 Webhook/SMTP，不上完整 APM，见 `docs/ONCALL.md`

只有**确实需要同时跑两个写进程**时，才另开一题：换 Postgres + 租约，让进行中的任务能被另一个进程领走。在那之前不要改存储和锁模型。

## P1 骨架明确不做

- 单测依赖真实 LLM API Key（测试用 stub LLM）
- 向量 RAG（留给 P4）

## 集成与产品化（集成 P3，已开）

与评测门禁 P3 区分，见 `docs/P3_INTEGRATION.md`。

- 正式对外路径 `/v1`；旧路径委托同一实现
- 模拟 Zendesk / 邮件网关：Webhook HMAC + Delivery-Id 幂等；结案出站回调
- **轻量多租户**：单实例、单 SQLite，`tenant_id` 列隔离数据与密钥，每租户队列配额；不做每租户独立库 / 多副本
