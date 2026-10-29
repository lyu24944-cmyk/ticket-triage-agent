# 构建审核台前端到 src/ticket_triage/api/static/review/
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $Root "web")
if (-not (Test-Path "node_modules")) {
    npm install --registry https://registry.npmmirror.com
}
npm run build
Write-Host "OK: built to src/ticket_triage/api/static/review/"
