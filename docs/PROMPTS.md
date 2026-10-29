# 提示词版本与回滚

模型路径版本号形如 `openai-act-v3`；stub 固定 `stub-act-v1`。

| 销钉 `PROMPT_PIN` | 实际修订 | 说明 |
|-------------------|----------|------|
| `current`（默认） | v3 | 用户消息经上下文预算裁剪：工单、成功工具结果、本次引用、上一步错误码 |
| `previous` | v2 | 用户消息更「整段」：act 列出已执行动作含失败原文；draft 只带工单与知识库 |

也可用 `v3` / `v2` / `openai-act-v2` 等别名。系统提示两版相同。

## 怎么切

```powershell
# 日常 / CI：只跑当前销钉（默认 current）
$env:PROMPT_PIN = "current"
python evals/run_evals.py

# 怀疑退回变多或工具选择掉了：切回上一版，再跑同一套黄金集
$env:PROMPT_PIN = "previous"
python evals/run_evals.py
```

**CI**（`.github/workflows/evals.yml`）仍是 stub 的 `pytest` + 一次 `run_evals.py`，跟仓库默认 `PROMPT_PIN=current`。不要在 CI 里对同一黄金集打两次外网模型。

## 对比两版（本地，可选）

```powershell
python evals/compare_prompt_versions.py
```

串行跑 current 与 previous，写出 `evals/prompt_compare.json`：类别、引用、工具选择、退回工单数。若退回变多或工具选择掉下去，报告会建议 `PROMPT_PIN=previous`。切回去即算回滚完成，**不要求再训练模型**。

## 影子评测（内测，只打分不写状态）

对新提示词抽样**真实工单正文**跑分类 / 工具 / 起草，**不入库、不写 pending、不改 tickets 状态**。

```powershell
# 默认抽 .data/tickets.db；没有真实单则回退黄金集正文（仍不写生产状态）
python evals/run_shadow.py --sample 20

# 对比两版销钉，看退回率信号
python evals/run_shadow.py --sample 20 --compare
```

写出 `evals/shadow_report.json`：`fallback_rate`（退回 stub 比例）、`recommend_pin`。退回变多或工具选择掉下去时设 `PROMPT_PIN=previous` 即回滚。
