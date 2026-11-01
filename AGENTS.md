# 给代理的短指引

长交接读 `docs/CODEX_HANDOFF.md`。动手前先打开下表对应的 `SKILL.md`，按其中的命令和边界做，不要凭记忆放宽门禁。

技能目录：`.cursor/skills/<name>/SKILL.md`。

| 技能 | 何时用 |
|------|--------|
| `verify-ticket-triage` | 合并前跑绿：默认 stub `pytest`（不含 integration）、黄金评测、红队；改了 `web/` 时 `npm run build`；依赖或镜像改动时对照 security CI |
| `secure-ops` | 改鉴权、密钥、`.env`、SQLite 数据目录、`instance.lock`、Docker / Trivy / 非 root |
| `next-work` | 选下一件作品集改进。P1–P7 与交接第 7 节已完成，不要重做 |
| `review-ui` | 改审核台、重建 `web/` 静态资源、确认 `GET /review` 仍可访问 |

硬约束（步骤在各技能里）：默认 CI 保持 stub；不提交 `.env`；同一数据目录只跑一个 API 进程；受理用 `SERVICE_API_KEY`，人审用人员 JWT，`REVIEW_API_KEY` 只用于换票。
