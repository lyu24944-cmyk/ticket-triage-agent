---
name: next-work
description: 选择下一件作品集改进，且不重做已完成的 P1–P7。Use when asked what to build next, to continue the portfolio, or to pick up CODEX_HANDOFF follow-ups. 非目标含 Postgres 多副本与真实 Zendesk OAuth。
---

# 下一件工作

先读 `docs/CODEX_HANDOFF.md` 第 4 节和第 7 节，以及 `docs/PORTFOLIO_ROADMAP.md`。P1–P7 和交接里的运维收口**已经在仓库里**。用户没点名之前，不要重做，也不要开大改。

## 已经做完（停手）

工具自选、审核台（`web/`，技能 `review-ui`）、MCP、黄金评测与红队、可恢复 `run_jobs`、经 `RAG_BASE_URL` 调用 citation-rag、人员 JWT / OIDC 换票关闭、影子评测（`evals/run_shadow.py`）、提示词销钉（`docs/PROMPTS.md`）、结案回调签名与重试、单进程锁、冷备份、值班告警、网关、安全扫描。

交接第 7 节写明这些都做完了。部署继续一个 uvicorn 进程、一份数据目录。

## 还值得做（先确认再动）

1. **作品集包装**（文档，不改运行时）  
   路线图第 12 周：简历 bullet、架构图、故障案例。README 已有架构图、失败模式表和简历句；`docs/P7_AGENT_PLAN.md` 有指标口径。下一小步是把对外叙述与仓库里的实测口径对齐，故障案例用现有失败模式表来写。不要为了包装改图或改评测门槛。

2. **模型治理产品化**（包一层已有能力，不重写评测）  
   已有：`PROMPT_PIN=current|previous`、`evals/compare_prompt_versions.py`、`evals/run_shadow.py`（只打分、不写状态）、fallback 记 `classify_model_failed` / `act_model_failed` / `draft_model_failed`。产品化是把这套回滚信号收成值班能执行的说明或只读入口。CI 仍只跑 stub + 当前销钉，不把外网模型打进 `.github/workflows/evals.yml`。见 `docs/PROMPTS.md`。

## 明确非目标

除非用户**明确要求**，否则不要做：

- Postgres、租约、多副本同时写同一份库（`docs/DECISIONS.md`）
- 真实 Zendesk / Jira OAuth。进线已是模拟 Webhook（`/v1/webhooks/*`），见 `docs/P3_INTEGRATION.md` 的非目标
- 重做 OIDC、影子评测、审核台、身份体系、Langfuse 接入
- 把 `citation-rag-agent` 并进本仓库（只通过 `RAG_BASE_URL` 调用）
- 完整 APM，或把 ops 日志改成合并门槛

## 怎么选

- 用户的句子若对应第 4 节某一行「完成」，回复现状和文档路径，不要再实现一遍。
- 没点名时，优先作品集包装，其次模型治理产品化。两件都先说范围再改。
- 代码改动仍要过 `.cursor/skills/verify-ticket-triage/SKILL.md`，并守住 `.cursor/skills/secure-ops/SKILL.md`。
