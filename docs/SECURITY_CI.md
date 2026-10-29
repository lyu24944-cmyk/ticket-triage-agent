# 安全扫描与非 root 门禁（P0.3）

上线前依赖与镜像漏洞进 CI；容器必须以 `appuser`（uid **10001**）运行。工作流：`.github/workflows/security.yml`。

## CI 做什么

| Job | 门禁 |
|-----|------|
| `deps-audit` | 升级 `pip>=26.2` 后，对 `pip freeze`（去掉本仓库 editable）跑 `pip-audit --strict` |
| `image-scan-and-nonroot` | `tests/check_dockerfile_nonroot.py`；`docker build`；`docker run … id -u` 必须为 `10001`；Trivy 对镜像 **CRITICAL/HIGH**（`ignore-unfixed: true`）失败即阻断 |

`evals.yml` 仍是 stub pytest + 黄金评测，不混进扫描，方便单独看红灯。

## 本地验收（可不启 Docker）

```powershell
.\.venv\Scripts\python.exe tests\check_dockerfile_nonroot.py
.\.venv\Scripts\python.exe -m pytest tests/test_image_security.py -q
```

有 Docker 时再加：

```powershell
docker build -t ticket-triage-agent:ci .
docker run --rm --entrypoint id ticket-triage-agent:ci -u   # 期望 10001
```

## Dockerfile 约定

- `useradd --uid 10001 … appuser`
- 最终指令是 `USER appuser`，其后不得再 `USER root`
- `deploy/docker-compose.yml` 不得写 `user: root` / `user: 0`
- 构建时升级 pip，减少把工具链漏洞打进镜像

## 不要做的事

- 不要为了过 Trivy 而关掉 `exit-code: "1"`（未修复的可用 `ignore-unfixed`，已覆盖）
- 不要在运行时 `docker run -u 0` 或 compose 里改回 root
- 不要把扫描失败改成只 warning 却仍合并
