# 备份、RPO/RTO 与季度演练（P0.4）

单实例 SQLite：冷备份前停掉唯一 API 进程，先 **WAL 收口** 再拷 `tickets.db` / `runtime.db`。**不拷** `instance.lock`。恢复与杀进程续跑步骤仍以 [P5_RUNBOOK.md](P5_RUNBOOK.md) 为准。

## 目标口径

| 指标 | 目标 | 含义 |
|------|------|------|
| **RPO** | **≤ 24 小时** | 按**每日**冷备份调度；最多丢最近一次成功备份之后的写入 |
| **RTO** | **≤ 1 小时** | 从选定备份覆盖两库、拉起单进程、用待审单验证人审可结案 |

若改为每小时备份，RPO 可收到 ≤ 1h，但仍是单实例，不要靠多副本缩短 RPO。

异地：`--offsite` 指向另一块盘 / 同步目录 / 对象存储挂载点；本地与异地各保留 `--keep` 份（默认 14 ≈ 两周日备）。

## 怎么跑

```powershell
# 1) 停掉唯一 API（本机进程或 compose）
# 2) 备份（可带异地）
.\.venv\Scripts\python.exe scripts\backup_sqlite.py --data-dir .data --dest .data\backups --offsite D:\triage-offsite --keep 14

# 或一键（会提示先停进程；compose 用 -UseCompose）
.\scripts\backup_cold.ps1 -Offsite "D:\triage-offsite"
.\scripts\backup_cold.ps1 -UseCompose -Offsite "D:\triage-offsite"
```

Windows 任务计划程序：每日（建议业务低峰）运行 `backup_cold.ps1`（`-SkipStop`/`-SkipStart` 仅当你用外部编排停启时）。Linux：`cron` 先 `systemctl stop` / `docker compose stop api`，再调 `backup_sqlite.py`，最后拉起。

产物：`.data/backups/backup-YYYYMMDD-HHMMSS/`（含 `MANIFEST.json` 与库文件）；异地为同名目录。

## 恢复（对齐 RTO）

1. 停 API。  
2. 用选定备份覆盖 `.data/tickets.db`、`.data/runtime.db`（有 `-wal`/`-shm` 一并覆盖或先删再拷）。不要恢复 `instance.lock`。  
3. 启动 API → `GET /health` → 抽待审单 `POST /runs/human`。  
4. 记时：从决定恢复到人审成功，目标 ≤ 1h。

详细步骤与历史演练表见 P5「备份和恢复演练」与 [DEMO.md](DEMO.md)。

## 季度演练（每年 ≥ 4 次）

按 P5 手册完整走一遍，并记入值班笔记：

1. **备份 / 覆盖**：停进程 → `backup_sqlite.py`（或手册手拷）→ 覆盖回库 → 待审单仍在、`schema_migrations` 含版本 2。  
2. **杀进程续跑**：`running` 时杀进程 → 重启后同 `trace_id` 到 `awaiting_human`，不是 `failed` / `run_interrupted`。  
3. **核对**：本次备份的 `MANIFEST.json`；异地目录有同名份；实测恢复耗时对照 **RTO ≤ 1h**；距上次成功备份对照 **RPO ≤ 24h**。  
4. 不要为此开第二个写进程或 scale API。

### 演练记录模板

| 项 | 填写 |
|----|------|
| 日期 | |
| 备份目录 / stamp | |
| 异地路径 | |
| 覆盖恢复是否成功 | |
| 杀进程续跑 trace_id | |
| 恢复耗时（对照 RTO） | |
| 操作人 | |

## 验收

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_backup_sqlite.py -q
# 手工：停 API 后跑 backup_sqlite.py，确认无 instance.lock、异地有副本、MANIFEST 齐全
```
