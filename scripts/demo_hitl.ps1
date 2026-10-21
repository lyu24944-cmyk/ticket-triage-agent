# 约 60 秒人审演示：创建工单并暂停，停掉 API，再启动后用同一 ticket_id 结案。
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

$ticketId = "demo-60s"
$base = "http://127.0.0.1:8000"

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

function Send-Json($method, $path, $payload) {
    $json = $payload | ConvertTo-Json -Depth 5 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    return Invoke-RestMethod -Method $method -Uri "$base$path" -ContentType "application/json; charset=utf-8" -Body $bytes
}

function Stop-Api($proc) {
    if ($null -eq $proc) { return }
    if (-not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force
        $proc.WaitForExit(5000) | Out-Null
    }
    Start-Sleep -Milliseconds 600
}

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
$created | ConvertTo-Json -Depth 6
if ($created.status -ne "awaiting_human") {
    Stop-Api $api
    throw "期望 awaiting_human，实际 $($created.status)"
}

Write-Host "3. 停掉 API 进程 $($api.Id)，内存里的待审记录随之消失"
Stop-Api $api

Write-Host "4. 重新启动 API，用同一个 ticket_id 人工通过"
$api = Start-Api
$resumed = Send-Json Post "/runs/human" @{
    ticket_id = $ticketId
    approved  = $true
}
$resumed | ConvertTo-Json -Depth 6
if ($resumed.status -ne "resolved") {
    Stop-Api $api
    throw "期望 resolved，实际 $($resumed.status)"
}

if ($created.trace_id) {
    Write-Host "5. 节点追踪 $($created.trace_id)"
    Invoke-RestMethod -Uri "$base/traces/$($created.trace_id)" | ConvertTo-Json -Depth 6
}

Stop-Api $api
Write-Host "演示结束：创建后暂停，进程重启后同一工单结案。"
