# Start citation-rag on 8001, run the P6 demo, then stop the server this script started.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$RagRoot = Join-Path (Split-Path -Parent $Root) "citation-rag-agent"
$RagPython = Join-Path $RagRoot ".venv\Scripts\python.exe"
$TicketPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $RagPython)) { throw "Missing $RagPython" }
if (-not (Test-Path $TicketPython)) { $TicketPython = "python" }

$started = $false
$proc = $null
try {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8001/health" -TimeoutSec 2
        if (-not $health.ok) { throw "health not ok" }
    } catch {
        $proc = Start-Process -FilePath $RagPython `
            -ArgumentList "-m", "uvicorn", "citation_rag.api.main:app", "--host", "127.0.0.1", "--port", "8001" `
            -WorkingDirectory $RagRoot -PassThru -WindowStyle Hidden
        $started = $true
        $ready = $false
        for ($i = 0; $i -lt 40; $i++) {
            try {
                $health = Invoke-RestMethod -Uri "http://127.0.0.1:8001/health" -TimeoutSec 2
                if ($health.ok) { $ready = $true; break }
            } catch {
                Start-Sleep -Milliseconds 500
            }
        }
        if (-not $ready) { throw "citation-rag did not become ready on port 8001" }
    }

    $env:RAG_BASE_URL = "http://127.0.0.1:8001"
    & $TicketPython (Join-Path $PSScriptRoot "demo_p6.py")
    if ($LASTEXITCODE -ne 0) { throw "demo_p6.py failed" }
} finally {
    Remove-Item Env:RAG_BASE_URL -ErrorAction SilentlyContinue
    if ($started -and $proc -and -not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force
    }
}
