# 作品集路线图（P1–P7）

本仓库从 **P1** 做到 **P7**。按下列顺序推进约 12 周，形成 Agent 工程师作品集。

| 编号 | 项目 | 本仓库状态 |
|------|------|------------|
| P1 | LangGraph 工单分流 + HITL + FastAPI | **已完成** |
| P2 | KB/工单的 MCP 工具服务 | **已完成**（见 `docs/MCP.md`） |
| P3 | 黄金评测 + CI 门禁 | **已完成**（含「不得伪造引用」回归） |
| P4 | 带引用的 RAG 问答 Agent | **句子嵌入已接入**（并列仓库 `citation-rag-agent`） |
| P5 | 可恢复人审 + 节点追踪 | **已完成**（见 `docs/P5_RUNBOOK.md`） |
| P6 | 垂直做深（工单域） | **已完成**：`RAG_BASE_URL` 把知识库检索接到 `citation-rag-agent`（见 `docs/P6.md`） |
| P7 | 白名单内自选工具 | **已完成**（见 `docs/P7_AGENT_PLAN.md`） |

真实模型路径已接上：`LLM_PROVIDER=openai|anthropic` 做分类、选工具和起草（`llm/model.py`）。默认和 CI 仍是 stub。DeepSeek / `openai-act-v3` 的全量数字在 `docs/P7_AGENT_PLAN.md`。

默认栈：Python 3.11+、LangGraph、FastAPI、MCP、Langfuse/LangSmith、Docker、GitHub Actions。

## 建议时间表

| 周次 | 产出 |
|------|------|
| 1–3 | P1 可跑通 + 基础追踪 |
| 4–5 | P2：MCP 接入 P1 |
| 6–8 | P3：评测 + CI 门禁（简历最大亮点） |
| 9–11 | P4 或 P5 二选一做深 |
| 12 | 简历 bullet、架构图、故障案例 blog |
