# 网关：TLS、内网、速率与 body 限制（P0.2）

应用只做第二道防线（鉴权、64KB body、`queue_full`）。**对外 TLS 终结、速率限制、统一 body 上限放在网关**；API 进程不直接对公网监听。仍是**单实例**：不要 `scale` API，也不要把同一份 `.data` 挂给第二个写进程。

## 拓扑

```text
客户端  --HTTPS:443-->  网关(nginx)  --HTTP-->  api:8000（内网 / compose 不 publish）
```

| 层 | 职责 |
|----|------|
| 网关 | TLS；`client_max_body_size 64000`；按 IP `limit_req`（`/runs` 更严）；HTTP→HTTPS |
| 应用 | `X-API-Key`；middleware 再拦一次 body；业务队列 `RUN_QUEUE_LIMIT` |
| 绑定 | 本机入口默认 `API_BIND_HOST=127.0.0.1`；容器内 `0.0.0.0` 但**只暴露给网关所在网络** |

常量源：`src/ticket_triage/limits.py` 的 `MAX_BODY_BYTES = 64000`，与 `deploy/nginx/nginx.conf` 必须一致。

## 本地一键（Docker）

```powershell
.\scripts\gen_gateway_certs.ps1
cd deploy
$env:SERVICE_API_KEY = "compose-service-key"
$env:REVIEW_API_KEY = "compose-review-key"
docker compose up --build -d
# 健康（忽略自签）
curl.exe -k https://127.0.0.1:8443/health
# 过大 body → 网关 413（到不了应用）
curl.exe -k -X POST https://127.0.0.1:8443/runs -H "Content-Type: application/json" -H "X-API-Key: compose-service-key" --data-binary "@big.json"
# 打满 /runs 速率 → 429（limit_req_status）
# 确认 8000 未映射：docker compose port api 8000 应失败
```

云厂商负载均衡 / API 网关：HTTPS 监听、后端指向单实例私网地址、请求体上限 64000 字节、对 `/runs` 做按调用方限流即可，不必换存储或加副本。

## 速率（默认，可按环境改 nginx）

| 路径 | zone | 速率 | burst |
|------|------|------|-------|
| `POST /runs`（`location = /runs`） | `triage_runs` | 20/min | 10 |
| 其余（含审核台） | `triage_general` | 60/min | 40 |
| `/health` | general | 同上，burst 30 | 便于探针 |

超限返回 **429**（不是 nginx 默认的 503）。业务队列满仍是应用的 `queue_full`，两者都是 429，靠响应体区分。

## 验收清单

1. 外网 / 本机只打到网关 `8443`（或云 LB 443）；API 的 `8000` 未对公网 publish。
2. 请求体 > 64000 字节：网关 **413**；等于应用 `payload_too_large` 口径。
3. 短时间连打 `POST /runs`：网关 **429**。
4. `GET https://…/health` 经 TLS 返回 200。
5. 单测：`pytest tests/test_gateway_config.py -q`（配置与 `MAX_BODY_BYTES` 对齐，无 Docker 也可跑）。

## 不要做的事

- 不要把容器 `8000` 映射到 `0.0.0.0:8000` 对公网开放
- 不要 `docker compose scale api=2` 或第二份进程写同一 SQLite
- 不要只在应用里做限流却关掉网关限流（网关是统一入口）
