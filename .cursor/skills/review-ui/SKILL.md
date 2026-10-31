---
name: review-ui
description: Build and smoke-check the human-review Vite UI under web/. Use when changing the review desk, rebuilding static assets, or verifying the HITL page still serves.
---

# Review UI

Source lives in `web/`. The committed build output is `src/ticket_triage/api/static/review/`. `GET /review` serves that `index.html`.

## Build

```bash
cd web && npm install && npm run build
```

`npm run build` is `tsc --noEmit && vite build`. Vite `base` is `/review/` and `outDir` is the static directory above (`emptyOutDir: true`).

## Contract the tests assert

- `GET /review` HTML contains `id="root"`, `/review/assets/`, and `工单审核台` (keep it in `web/index.html` `<title>`).
- Built JS still contains `分类模型失败` and `草稿模型失败`.
- Human review calls send `Authorization: Bearer`. `REVIEW_API_KEY` is only the `X-API-Key` on `POST /v1/auth/review/token`. `SERVICE_API_KEY` is only the `X-API-Key` on `/v1/runs`. Do not inline secret values.

## Smoke

```bash
pytest -q tests/test_review.py tests/test_review_static.py
```

Local desk: one API process, `--workers 1`, and its own data directory. Do not start a second writer against `.data/` / `instance.lock`.

```bash
TICKET_DB_PATH=/tmp/review-ui/tickets.db RUNTIME_DB_PATH=/tmp/review-ui/runtime.db \
  uvicorn ticket_triage.api.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Open `http://127.0.0.1:8000/review`.
