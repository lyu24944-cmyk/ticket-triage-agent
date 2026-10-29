# 生成本地自签证书，供 deploy/docker-compose 网关 TLS 演练。不入 git。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$certDir = Join-Path $root "deploy\certs"
New-Item -ItemType Directory -Force -Path $certDir | Out-Null
$crt = Join-Path $certDir "tls.crt"
$key = Join-Path $certDir "tls.key"

function Invoke-OpenSsl([string[]]$OpenSslArgs) {
    & openssl @OpenSslArgs
    if ($LASTEXITCODE -ne 0) { throw "openssl failed: $OpenSslArgs" }
}

if (Get-Command openssl -ErrorAction SilentlyContinue) {
    Invoke-OpenSsl @(
        "req", "-x509", "-nodes", "-newkey", "rsa:2048", "-days", "365",
        "-keyout", $key, "-out", $crt,
        "-subj", "/CN=localhost/O=ticket-triage-agent/C=CN"
    )
    Write-Host "wrote $crt and $key"
    exit 0
}

if (Get-Command docker -ErrorAction SilentlyContinue) {
    $mount = ($certDir -replace "\\", "/")
    docker run --rm -v "${certDir}:/certs" alpine/openssl req -x509 -nodes -newkey rsa:2048 -days 365 `
        -keyout /certs/tls.key -out /certs/tls.crt `
        -subj "/CN=localhost/O=ticket-triage-agent/C=CN"
    if ($LASTEXITCODE -ne 0) { throw "docker openssl failed" }
    Write-Host "wrote $crt and $key (via docker alpine/openssl)"
    exit 0
}

Write-Error "Need openssl on PATH, or Docker, to generate deploy/certs/tls.crt and tls.key"
