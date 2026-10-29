# 运行工单分流 API。进程以 appuser 启动，并且只开一个 worker。
FROM python:3.12-slim

WORKDIR /app
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser
COPY pyproject.toml README.md ./
COPY src ./src
COPY mcp_server ./mcp_server
# 构建阶段升级 pip，避免把已知漏洞的旧 pip 带进镜像；最终 USER 必须是 appuser（CI 门禁）。
RUN python -m pip install --no-cache-dir -U "pip>=26.2" \
    && pip install --no-cache-dir -e . \
    && mkdir -p /app/.data \
    && chown -R appuser:appuser /app

USER appuser
# 容器网络内监听；不要把 8000 publish 到公网，前面加 TLS 网关，见 docs/GATEWAY.md / deploy/docker-compose.yml
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["uvicorn", "ticket_triage.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
