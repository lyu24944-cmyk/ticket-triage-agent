# 第一周清单（P1）

- [x] 锁定业务域：工单分流 Agent + 技术栈（`docs/DECISIONS.md`）
- [x] 搭建 LangGraph 图 + FastAPI + stub LLM
- [x] 无需 API Key 的单元测试
- [x] 本地跑通 `pytest` 并处理环境问题（13 passed；`python evals/run_evals.py` 门禁通过）
- [x] 录制约 60 秒 Demo：创建 run → awaiting_human → 停进程 → 同一 `ticket_id` 审批 → resolved（`scripts/demo_hitl.ps1`，记录见 `docs/DEMO.md`）
- [ ] （可选）在 `llm_provider=openai|anthropic` 后接真实模型
- [x] （可选）把架构图补进 README
- [x] 推到 GitHub 并加上 LICENSE

## 「P1 完成」验收标准

1. `pytest` 全绿  
2. `/runs` 返回分类结果 + 草稿  
3. HITL 暂停 + `/runs/human` 恢复可用  
4. README 含失败模式表
