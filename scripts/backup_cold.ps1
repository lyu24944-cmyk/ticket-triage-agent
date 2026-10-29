# 冷备份：先停 API → WAL 收口并拷贝 → 可选拉起。见 docs/BACKUP.md
# 例：
#   .\scripts\backup_cold.ps1
#   .\scripts\backup_cold.ps1 -Offsite "D:\triage-offsite" -UseCompose
param(
    [string]$DataDir = ".data",
    [string]$Dest = ".data\backups",
    [string]$Offsite = "",
    [int]$Keep = 14,
    [switch]$UseCompose,
    [switch]$SkipStop,
    [switch]$SkipStart
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

if (-not $SkipStop) {
    if ($UseCompose) {
        Push-Location (Join-Path $root "deploy")
        try {
            docker compose stop api
        } finally {
            Pop-Location
        }
    } else {
        Write-Host "Ensure the single API process is stopped, then press Enter..."
        [void](Read-Host)
    }
}

$args = @(
    (Join-Path $root "scripts\backup_sqlite.py"),
    "--data-dir", $DataDir,
    "--dest", $Dest,
    "--keep", "$Keep"
)
if ($Offsite) {
    $args += @("--offsite", $Offsite)
}

& $py @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not $SkipStart) {
    if ($UseCompose) {
        Push-Location (Join-Path $root "deploy")
        try {
            docker compose start api
        } finally {
            Pop-Location
        }
    }
}

Write-Host "backup_ok"
