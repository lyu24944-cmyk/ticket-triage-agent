# 项目决策（已锁定）

## P1 业务域

**工单分流 Agent（Ticket Triage Agent）**  
流程：入站客服工单 → 分类 → 工具 enrichment → 起草回复 → 人工审核通过 → 结案或升级。

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

## P1 骨架明确不做

- 单测依赖真实 LLM API Key（测试用 stub LLM）
- 生产鉴权 / 多租户
- 向量 RAG（留给 P4）
