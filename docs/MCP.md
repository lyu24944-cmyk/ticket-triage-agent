# P2 MCP 使用说明

## 启动 MCP Server（stdio）

```powershell
cd C:\Users\Administrator\Projects\ticket-triage-agent
.\.venv\Scripts\activate
python -m mcp_server
```

该进程会挂起等待 Host 通过 stdin/stdout 通信（正常现象）。

冒烟（只打印工具目录，不进入 stdio 循环）：

```powershell
python -m mcp_server.server
```

## Agent 开启 USE_MCP

```powershell
$env:USE_MCP="true"
$env:MCP_TIMEOUT_SECONDS="5"
uvicorn ticket_triage.api.main:app --reload --port 8000
```

此时 `enrich` / `finalize` 的工具调用经 MCP stdio 客户端完成；工单数据落在共享 SQLite（`TICKET_DB_PATH` 或默认 `.data/tickets.db`）。

## Cursor / Claude Desktop 配置片段

将路径改成你的本机绝对路径：

```json
{
  "mcpServers": {
    "ticket-triage-tools": {
      "command": "C:\\Users\\Administrator\\Projects\\ticket-triage-agent\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp_server"],
      "cwd": "C:\\Users\\Administrator\\Projects\\ticket-triage-agent",
      "env": {
        "PYTHONPATH": "C:\\Users\\Administrator\\Projects\\ticket-triage-agent\\src;C:\\Users\\Administrator\\Projects\\ticket-triage-agent"
      }
    }
  }
}
```

## 安全模型

- 只暴露 3 个工具：检索 KB、读历史、更新状态
- **无** shell、文件系统任意读写、SQL 执行工具
- `update_ticket_status` 的 `status` 白名单：`open|resolved|escalated|pending`
- 写操作打审计日志（logger：`ticket_triage.mcp`）
- 客户端超时默认 5 秒，超时进入图的软降级（无 KB 起草）

## 验收对照

| 标准 | 验证方式 |
|------|----------|
| 注册 3 工具 | `pytest -k lists_tools` / Cursor 连接后 list tools |
| USE_MCP 形态一致 | `pytest -m integration` |
| 非法 status 拒绝 | `test_mutating_tool_rejects_unknown_status` |
| 超时软降级 | `test_enrich_soft_fallback_on_mcp_timeout` |
