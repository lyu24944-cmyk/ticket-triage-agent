# 工单审核台（Vite + React）

窄范围：登录（mint / OIDC）、待审与已处理分页、详情人审、演示新建工单。API 一律 `/v1`。

```powershell
npm install
npm run dev      # http://127.0.0.1:5173/review/ ，代理到 API :8000
npm run build    # 输出到 ../src/ticket_triage/api/static/review/
```

生产由 FastAPI `GET /review` 托管构建产物。仓库根目录也可用 `scripts/build_review.ps1`。
