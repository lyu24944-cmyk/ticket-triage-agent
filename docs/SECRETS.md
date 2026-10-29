# 密钥管理与轮换（P0.1）

受理密钥 `SERVICE_API_KEY` 与审核密钥 `REVIEW_API_KEY` 不得打进镜像，不得出现在日志。生产用云厂商 Secret Manager（或 K8s Secret / Docker secret）把明文挂成文件，进程用 `*_FILE` 读取。

## 怎么注入

| 变量 | 含义 |
|------|------|
| `SERVICE_API_KEY` / `REVIEW_API_KEY` | 仅本地开发；生产不要写进镜像或编排明文 |
| `SERVICE_API_KEY_FILE` / `REVIEW_API_KEY_FILE` | 指向挂载文件；**文件优先于环境变量** |
| `SERVICE_API_KEY_PREVIOUS` / `REVIEW_API_KEY_PREVIOUS` | 轮换窗口里的上一把 |
| `*_PREVIOUS_FILE` | 上一把的文件挂载 |

示例（Secret Manager → 文件 → 环境）：

```text
# 编排把 Secret Manager 的版本挂到 /run/secrets/...
SERVICE_API_KEY_FILE=/run/secrets/service_api_key
REVIEW_API_KEY_FILE=/run/secrets/review_api_key
```

镜像构建：`Dockerfile` 不 `COPY .env`；`.dockerignore` 排除 `.env` 与 `.data`。容器以 `appuser` 运行，密钥只经运行时挂载进入。

## 轮换步骤（可验收）

1. 在 Secret Manager 生成新版本，把**旧值**写到 `SERVICE_API_KEY_PREVIOUS`（或 `*_PREVIOUS_FILE`），**新值**写到当前 `SERVICE_API_KEY` / `*_FILE`。审核密钥同理。
2. 不重启也可：鉴权每次解析文件；若只改了环境变量则需重启进程。
3. 用新旧两把密钥分别调用 `POST /runs`（受理）与 `GET /runs/pending`（审核），都应 202 / 200，不得 401。
4. 调用方全部切到新密钥后，清空 `*_PREVIOUS*`，旧密钥应 401 `*_auth_failed`。
5. 搜日志：`Select-String` / `grep` 不得命中密钥明文；误打日志时会被换成 `[REDACTED_SECRET]`。

### 本地演练（无需云）

```powershell
$env:SERVICE_API_KEY = "new-service"
$env:SERVICE_API_KEY_PREVIOUS = "old-service"
$env:REVIEW_API_KEY = "new-review"
# 启动 API 后：
Invoke-RestMethod http://127.0.0.1:8000/runs -Method POST -Headers @{ "X-API-Key" = "old-service" } -ContentType "application/json" -Body '{"ticket":{"ticket_id":"rot-1","subject":"x","body":"重复扣款退款"}}'
Invoke-RestMethod http://127.0.0.1:8000/runs -Method POST -Headers @{ "X-API-Key" = "new-service" } -ContentType "application/json" -Body '{"ticket":{"ticket_id":"rot-2","subject":"x","body":"重复扣款退款"}}'
# 去掉 PREVIOUS 后 old-service 应 401
```

单测：`tests/test_secrets.py`（文件注入、轮换窗口、日志脱敏、settings repr 不含明文）。

## 不要做的事

- 不要把 `.env` 打进镜像或提交进 git
- 不要在结构化日志、异常、`GET /health` 里回显密钥
- 不要用审核密钥调受理接口，也不要反过来
