# 启动本地 n8n（Docker）
# 用法：powershell -ExecutionPolicy Bypass -File start-n8n.ps1

$ErrorActionPreference = 'Stop'
$name = 'yingliu-n8n'
$port = 5678

# 1. 确保 Docker Desktop 在跑
$daemon = docker info --format '{{.ServerVersion}}' 2>$null
if (-not $daemon -or $daemon -match 'failed') {
    Write-Host 'Docker 未就绪，尝试启动 Docker Desktop...'
    $exe = "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe"
    if (Test-Path $exe) { Start-Process $exe }
    $deadline = (Get-Date).AddSeconds(180)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 6
        $daemon = docker info --format '{{.ServerVersion}}' 2>$null
        if ($daemon -and $daemon -notmatch 'failed') { break }
    }
    if (-not $daemon -or $daemon -match 'failed') { throw 'Docker 引擎在 180 秒内未就绪' }
}
Write-Host "Docker engine: $daemon"

# 2. 复用或创建容器
$exists = docker ps -a --filter "name=^/$name$" --format '{{.Names}}'
if ($exists -eq $name) {
    $running = docker ps --filter "name=^/$name$" --format '{{.Names}}'
    if ($running -ne $name) { docker start $name | Out-Null; Write-Host '已启动已有容器' }
    else { Write-Host '容器已在运行' }
} else {
    docker run -d --name $name --restart unless-stopped `
        -p "${port}:5678" `
        -v n8n_data:/home/node/.n8n `
        -e N8N_SECURE_COOKIE=false `
        -e GENERIC_TIMEZONE=Asia/Shanghai `
        -e TZ=Asia/Shanghai `
        n8nio/n8n | Out-Null
    Write-Host '已创建并启动容器'
}

# 3. 等待 HTTP 就绪
$deadline = (Get-Date).AddSeconds(120)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:$port/healthz" -TimeoutSec 5 -UseBasicParsing
        if ($r.StatusCode -eq 200) { break }
    } catch { }
}
Write-Host ''
Write-Host "n8n 地址: http://localhost:$port"
Write-Host '首次访问需要创建管理员账号（邮箱 + 密码），之后即可在 UI 里查看工作流。'
