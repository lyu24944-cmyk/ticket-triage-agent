# 约 60 秒人审演示：创建工单并暂停，停掉 API，再启动后用同一 ticket_id 结案。
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

$ticketId = "demo-60s"
$helloId = "demo-hello"
$base = "http://127.0.0.1:8000"
# 非空的 local 会覆盖 .env 里的远程地址，检索走仓库里的三篇关键词文档。
# 去掉进程里的 LLM_PROVIDER，让 .env 决定用 stub 还是真实模型。
if (Test-Path Env:LLM_PROVIDER) { Remove-Item Env:LLM_PROVIDER }
$env:RAG_BASE_URL = "local"
if (-not $env:REVIEW_API_KEY) {
    $envFile = Join-Path $Root ".env"
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile) {
            if ($line -match '^\s*REVIEW_API_KEY=(.*)$') {
                $env:REVIEW_API_KEY = $Matches[1].Trim().Trim('"').Trim("'")
                break
            }
        }
    }
}
if (-not $env:REVIEW_API_KEY) { $env:REVIEW_API_KEY = "demo-review-key" }
$reviewHeaders = @{ "X-API-Key" = $env:REVIEW_API_KEY }

function Wait-Health {
    for ($i = 0; $i -lt 30; $i++) {
        try {
            $health = Invoke-RestMethod -Uri "$base/health" -TimeoutSec 2
            if ($health.ok) { return }
        } catch {
            Start-Sleep -Milliseconds 400
        }
    }
    throw "API 未在 8000 端口就绪"
}

function Start-Api {
    $proc = Start-Process -FilePath $python `
        -ArgumentList "-m", "uvicorn", "ticket_triage.api.main:app", "--host", "127.0.0.1", "--port", "8000" `
        -WorkingDirectory $Root `
        -PassThru `
        -WindowStyle Hidden
    Wait-Health
    return $proc
}

function Wait-Run($created) {
    if ($created.status -ne "running") { return $created }
    for ($i = 0; $i -lt 240; $i++) {
        $current = Invoke-RestMethod -Uri "$base/runs/$($created.trace_id)"
        if ($current.status -eq "failed") {
            throw "运行失败: $($current.errors -join ', ')"
        }
        if ($current.status -ne "running") { return $current }
        Start-Sleep -Milliseconds 500
    }
    throw "运行超时 $($created.trace_id)"
}

function Send-Json($method, $path, $payload) {
    $json = $payload | ConvertTo-Json -Depth 5 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    return Invoke-RestMethod -Method $method -Uri "$base$path" -Headers $reviewHeaders -ContentType "application/json; charset=utf-8" -Body $bytes
}

function Stop-Api($proc) {
    if ($null -eq $proc) { return }
    if (-not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force
        $proc.WaitForExit(5000) | Out-Null
    }
    Start-Sleep -Milliseconds 600
}

Write-Host "0. 清掉上一次演示留下的 $ticketId 和 $helloId"
& $python -c "from ticket_triage.graph.workflow import discard_ticket; discard_ticket('demo-60s'); discard_ticket('demo-hello')"
if ($LASTEXITCODE -ne 0) { throw "清理演示工单失败" }

Write-Host "1. 启动 API"
$api = Start-Api

Write-Host "2. 创建工单 $ticketId ，应停在 awaiting_human"
$created = Send-Json Post "/runs" @{
    ticket = @{
        ticket_id = $ticketId
        subject   = "紧急退款"
        body      = "我被重复扣款，需要退款"
    }
    auto_approve = $false
}
$created = Wait-Run $created
$created | ConvertTo-Json -Depth 6
if ($created.status -ne "awaiting_human") {
    Stop-Api $api
    throw "期望 awaiting_human，实际 $($created.status)"
}
$createdPath = Join-Path $env:TEMP "demo_refund.json"
$created | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 $createdPath
& $python (Join-Path $Root "scripts\score_run.py") refund $createdPath
if ($LASTEXITCODE -ne 0) {
    Stop-Api $api
    throw "退款单打分未通过"
}
$closedPath = Join-Path $env:TEMP "demo_tickets_before.json"
Invoke-WebRequest -Uri "$base/tickets" -Headers $reviewHeaders -OutFile $closedPath
& $python (Join-Path $Root "scripts\score_run.py") absent $closedPath $ticketId
if ($LASTEXITCODE -ne 0) {
    Stop-Api $api
    throw "通过之前工单不应出现在已处理列表"
}

Write-Host "3. 停掉 API 进程 $($api.Id)，内存里的待审记录随之消失"
Stop-Api $api

Write-Host "4. 重新启动 API，用同一个 ticket_id 人工通过"
$api = Start-Api
$resumed = Send-Json Post "/runs/human" @{
    ticket_id = $ticketId
    approved  = $true
    reviewer  = "demo"
}
$resumed | ConvertTo-Json -Depth 6
if ($resumed.status -ne "resolved") {
    Stop-Api $api
    throw "期望 resolved，实际 $($resumed.status)"
}

$afterPath = Join-Path $env:TEMP "demo_tickets_after.json"
Invoke-WebRequest -Uri "$base/tickets" -Headers $reviewHeaders -OutFile $afterPath
& $python (Join-Path $Root "scripts\score_run.py") resolved $afterPath $ticketId
if ($LASTEXITCODE -ne 0) {
    Stop-Api $api
    throw "结案状态或历史不符合 open → resolved"
}

Write-Host "5. 问候单 $helloId 只结束，不检索"
$hello = Send-Json Post "/runs" @{
    ticket = @{
        ticket_id = $helloId
        subject   = "Hello"
        body      = "Just saying hi"
    }
    auto_approve = $false
}
$hello = Wait-Run $hello
$hello | ConvertTo-Json -Depth 6
$helloPath = Join-Path $env:TEMP "demo_hello.json"
$hello | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 $helloPath
& $python (Join-Path $Root "scripts\score_run.py") greeting $helloPath
if ($LASTEXITCODE -ne 0) {
    Stop-Api $api
    throw "问候单打分未通过"
}

if ($created.trace_id) {
    Write-Host "6. 节点追踪 $($created.trace_id)"
    Invoke-RestMethod -Uri "$base/traces/$($created.trace_id)" -Headers $reviewHeaders | ConvertTo-Json -Depth 6
}

Stop-Api $api
Write-Host "演示结束：本地知识库检索成功，通过前未结案，重启后同一工单结案，问候单不检索。"
