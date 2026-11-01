---
name: verify-ticket-triage
description: 合并前跑绿门禁。Use before merge, or when running pytest, golden evals, redteam, the web/ review build, or checking image-scan / security CI. Default pytest is stub and excludes integration.
---

# 合并前绿门禁

改完先跑下面这组。默认路径不需要 API Key，也不打外网模型。没改到的门禁不要顺手扩范围。

## 必绿（与 `.github/workflows/evals.yml` 一致）

```bash
pip install -e ".[dev]"
pytest -q
python evals/run_evals.py
python evals/run_redteam.py
```

- `pytest` / `pytest -q` 读 `pyproject.toml` 的 `addopts = -m 'not integration'`，只跑 stub 单测。夹具在 `tests/conftest.py` 里强制 `llm_provider=stub`。
- 只有改了 MCP stdio 行为时才加 `pytest -m integration`。这不是默认合并门槛。
- `evals/run_evals.py` 在无 Key 时走 stub。CI 设置 `PROMPT_PIN=current`。不要在 CI 里跑 `evals/compare_prompt_versions.py`（会打两次外网模型）。
- 黄金评测：模型路径上任一 fallback 算失败；stub 路径不因 `*_model_failed` 字符串失败。p95 只记录，不卡 2 秒。
- 值班日志、Webhook、Langfuse 不进合并门槛。见 `docs/ONCALL.md`、`docs/CODEX_HANDOFF.md` 第 7 节。

## 改了审核台才构建

源码在 `web/`，产物提交在 `src/ticket_triage/api/static/review/`（Vite `base` 为 `/review/`，`outDir` 指向该目录）。CI 不跑 npm；不提交构建，`tests/test_review_static.py` 会红。

```bash
cd web && npm install && npm run build
pytest -q tests/test_review.py tests/test_review_static.py
```

`npm run build` 是 `tsc --noEmit && vite build`。页面契约见 `.cursor/skills/review-ui/SKILL.md`。

## 安全 CI（`.github/workflows/security.yml`，与评测分开）

| Job | 合并时必须绿 |
|-----|----------------|
| `deps-audit` | `pip-audit --strict`（排除本仓库 editable） |
| `image-scan-and-nonroot` | `tests/check_dockerfile_nonroot.py`；镜像 uid **10001**；Trivy **CRITICAL/HIGH**（`ignore-unfixed: true`，`exit-code: 1`） |

本机没有 Docker 时，至少：

```bash
python tests/check_dockerfile_nonroot.py
pytest -q tests/test_image_security.py
```

有 Docker 时的构建与 `id -u` 命令见 `docs/SECURITY_CI.md`。不要为了过扫描关掉 Trivy 的 `exit-code`，也不要把失败改成 warning 再合并。

## 合并前停手条件

evals 三条命令退出码为 0。动过 `web/` 或静态产物时，审核台测试为 0。动过依赖、`Dockerfile` 或 `deploy/docker-compose.yml` 时，安全门禁里对应的检查为 0。不要把 `.env`、`.data/`、`evals/last_report.json` 带进提交。
