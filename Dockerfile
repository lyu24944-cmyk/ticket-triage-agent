# 运行工单分流 API
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e .

EXPOSE 8000
CMD ["uvicorn", "ticket_triage.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
