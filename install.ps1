# Augura 一键安装（Windows）——install.bat 的全部逻辑在这里。
#
# 用法：双击仓库根目录的 install.bat，或在 PowerShell 中执行本脚本。
# 幂等，可重复运行（重复运行 = 更新镜像并重启）。
#
# 流程：检测/安装 Docker Desktop → 检测/修复 WSL2 → 启动引擎
#       → 下载 docker-compose.yml + .env.example → 生成 .env（随机密码）
#       → 拉取预构建镜像（失败自动重试/可配加速器）→ 健康检查 → 打开浏览器。

$ErrorActionPreference = 'Stop'

$RepoRaw   = if ($env:AUGURA_REPO_RAW)   { $env:AUGURA_REPO_RAW }   else { 'https://raw.githubusercontent.com/augura-os/augura/main' }
$ComposeUrl = if ($env:AUGURA_COMPOSE_URL) { $env:AUGURA_COMPOSE_URL } else { "$RepoRaw/docker-compose.yml" }
$EnvUrl     = if ($env:AUGURA_ENV_URL)     { $env:AUGURA_ENV_URL }     else { "$RepoRaw/.env.example" }
$HomeDir    = if ($env:AUGURA_HOME)      { $env:AUGURA_HOME }      else { Join-Path $env:USERPROFILE 'augura' }
$ImageMirror = if ($env:AUGURA_IMAGE_MIRROR) { $env:AUGURA_IMAGE_MIRROR } else { 'ghcr.nju.edu.cn' }
$GhcrImages = @('augura-os/augura-api:latest', 'augura-os/augura-web:latest')

$DockerBin = 'C:\Program Files\Docker\Docker\resources\bin'

function Info($msg) { Write-Host "[*] $msg" }
function Ok($msg)   { Write-Host "[OK] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Fatal($msg) {
    Write-Host "[x] $msg" -ForegroundColor Red
    Read-Host '按回车退出'
    exit 1
}

function Refresh-DockerPath {
    if (Test-Path $DockerBin) {
        $env:Path = "$env:Path;$DockerBin"
    }
}

# PS 5.1 在 $ErrorActionPreference='Stop' 下会把外部命令的 stderr 当成异常
# （NativeCommandError），外部命令统一走这个包装器，返回真实退出码。
function Invoke-Native([scriptblock]$Cmd) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $Cmd
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    return $code
}

function Test-DockerEngine {
    Refresh-DockerPath
    $null = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $?) { return $false }
    docker info 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

Write-Host '============================================================'
Write-Host '  Augura 一键安装向导（Windows）'
Write-Host '  数据存在你自己的机器上，不上传任何素材与投放明细'
Write-Host '============================================================'
Write-Host ''

# --- 1. Docker Desktop 检测与安装 ------------------------------------------
Refresh-DockerPath
$dockerExe = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerExe -and -not (Test-Path "$DockerBin\docker.exe")) {
    Warn '未检测到 Docker Desktop，将使用 winget 自动安装（可能弹出授权窗口，请允许）'
    $answer = Read-Host '继续安装 Docker Desktop？(Y/n)'
    if ($answer -match '^[nN]') { Fatal '已取消。手动安装：https://www.docker.com/products/docker-desktop/' }
    # 明确指定 --source winget：默认 msstore 源在部分网络下会报证书错误（0x8a15005e）
    if ((Invoke-Native { winget install -e --id Docker.DockerDesktop --source winget --accept-source-agreements --accept-package-agreements }) -ne 0) { Fatal 'winget 安装失败，请手动安装 Docker Desktop 后重试' }
    Refresh-DockerPath
    Ok 'Docker Desktop 安装完成'
}

# --- 2. WSL2 检测与修复（Docker Desktop 的 Linux 引擎依赖商店版 WSL2） --------
$wslOk = $false
$wslCmd = Get-Command wsl.exe -ErrorAction SilentlyContinue
if ($wslCmd) {
    wsl.exe --version 2>$null | Out-Null
    $wslOk = ($LASTEXITCODE -eq 0)
}
if (-not $wslOk) {
    Warn '未检测到商店版 WSL2（Docker Desktop 必需）。开始自动修复...'

    Info '启用 Windows 功能：虚拟机平台 + 适用于 Linux 的 Windows 子系统（会弹授权窗口）'
    $dism = Start-Process powershell -Verb RunAs -Wait -PassThru -ArgumentList '-NoProfile','-Command',
        'dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart; dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart'
    if ($dism.ExitCode -ne 0) { Warn '功能启用可能未完全成功，继续尝试安装 WSL2 主程序' }

    Info '下载 WSL2 安装包（GitHub 官方 release，直连下载）...'
    $msiUrl = $null
    try {
        $rel = Invoke-RestMethod 'https://api.github.com/repos/microsoft/WSL/releases/latest' -TimeoutSec 30
        $msiUrl = ($rel.assets | Where-Object { $_.name -match '^wsl\..*x64\.msi$' } | Select-Object -First 1).browser_download_url
    } catch { Warn '获取最新版本号失败，使用已知稳定版本' }
    if (-not $msiUrl) { $msiUrl = 'https://github.com/microsoft/WSL/releases/download/2.7.13/wsl.2.7.13.0.x64.msi' }

    $msiPath = Join-Path $env:TEMP 'wsl_x64.msi'
    # 先直连（release-assets 通常直连更快），失败再走系统代理重试
    if ((Invoke-Native { curl.exe -fSL --noproxy '*' -o $msiPath $msiUrl }) -ne 0) {
        Warn '直连下载失败，改用系统代理重试...'
        if ((Invoke-Native { curl.exe -fSL -o $msiPath $msiUrl }) -ne 0) {
            Fatal "WSL2 安装包下载失败。请手动下载安装后重试：$msiUrl"
        }
    }

    Info '安装 WSL2 主程序（会弹授权窗口）...'
    $msi = Start-Process msiexec.exe -Verb RunAs -Wait -PassThru -ArgumentList '/i', "`"$msiPath`"", '/qn', '/norestart'
    if ($msi.ExitCode -ne 0) { Fatal "WSL2 安装失败（msiexec 退出码 $($msi.ExitCode)），请手动双击 $msiPath 安装后重试" }
    Ok 'WSL2 安装完成'

    Write-Host ''
    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host '  WSL2 组件已装好，但需要【重启电脑】才能生效。' -ForegroundColor Cyan
    Write-Host '  重启后再次双击 install.bat 即可继续（已完成的步骤会自动跳过）。' -ForegroundColor Cyan
    Write-Host '============================================================' -ForegroundColor Cyan
    Read-Host '按回车退出（请重启电脑）'
    exit 0
}
Ok 'WSL2 就绪'

# --- 3. 启动 Docker 引擎 ------------------------------------------------------
if (-not (Test-DockerEngine)) {
    Info '启动 Docker Desktop，等待引擎就绪...'
    $desktopExe = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
    if (Test-Path $desktopExe) { Start-Process $desktopExe }
    $waited = 0
    while (-not (Test-DockerEngine)) {
        Start-Sleep -Seconds 5
        $waited += 5
        if ($waited -ge 240) {
            Fatal 'Docker 引擎 4 分钟内未就绪。若 Docker Desktop 提示 "Virtualization support not detected"，说明 WSL2/虚拟机平台未生效——请重启电脑后重试；仍不行则需进入 BIOS 开启 CPU 虚拟化（VT-x/AMD-V）'
        }
        if (($waited % 20) -eq 0) { Info "等待 Docker 引擎启动... ${waited}s" }
    }
}
Ok 'Docker 引擎运行中'

# --- 4. 下载最小文件集 ---------------------------------------------------------
New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null
Set-Location $HomeDir

Info "下载部署文件到 $HomeDir ..."
# 主源失败时回退 jsdelivr（raw.githubusercontent.com 国内经常不通）。
# 仅对默认 GitHub raw 源生效，自定义 AUGURA_COMPOSE_URL/AUGURA_ENV_URL 的自己负责可达性。
function Get-RemoteFile([string]$Url, [string]$Dest) {
    if ((Invoke-Native { curl.exe -fsSL $Url -o $Dest }) -eq 0) { return }
    if ($Url -like 'https://raw.githubusercontent.com/augura-os/augura/*') {
        $path = $Url -replace '^https://raw\.githubusercontent\.com/augura-os/augura/', ''
        $branch = $path.Split('/')[0]
        $rest = $path.Substring($branch.Length + 1)
        $mirror = "https://cdn.jsdelivr.net/gh/augura-os/augura@$branch/$rest"
        Warn "主源下载失败，回退 jsdelivr 镜像：$mirror"
        if ((Invoke-Native { curl.exe -fsSL $mirror -o $Dest }) -eq 0) { return }
    }
    Fatal "$Dest 下载失败（$Url）"
}
Get-RemoteFile $ComposeUrl 'docker-compose.yml'
Get-RemoteFile $EnvUrl '.env.example'
Ok '部署文件就绪'

# --- 5. 环境文件（首启生成随机密码） ---------------------------------------------
function New-RandomPassword {
    -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })
}

if (-not (Test-Path .env)) {
    $pg = New-RandomPassword
    $neo4j = New-RandomPassword
    $minioRoot = New-RandomPassword
    $minioApp = New-RandomPassword
    $content = Get-Content .env.example -Raw
    $content = $content -replace '(?m)^POSTGRES_PASSWORD=augura$', "POSTGRES_PASSWORD=$pg"
    $content = $content -replace '(?m)^NEO4J_AUTH=neo4j/augura123$', "NEO4J_AUTH=neo4j/$neo4j"
    $content = $content -replace '(?m)^NEO4J_PASSWORD=augura123$', "NEO4J_PASSWORD=$neo4j"
    $content = $content -replace '(?m)^MINIO_ROOT_PASSWORD=augura123$', "MINIO_ROOT_PASSWORD=$minioRoot"
    $content = $content -replace '(?m)^MINIO_APP_PASSWORD=changeme-app$', "MINIO_APP_PASSWORD=$minioApp"
    [System.IO.File]::WriteAllText((Join-Path (Get-Location) '.env'), $content, [System.Text.UTF8Encoding]::new($false))
    Ok '已创建 .env（含随机生成的数据库密码；AI Key 可稍后在 Settings 页配置）'
} else {
    Ok '.env 已存在'
}

# --- 6. 拉取镜像（自动重试；多次失败回退 ghcr 镜像站 / 国内加速器） -------------------
function Invoke-ComposePull {
    return ((Invoke-Native { docker compose pull }) -eq 0)
}

function Install-ImagesViaMirror {
    Info "尝试通过镜像站 $ImageMirror 拉取 ghcr 镜像..."
    foreach ($img in $GhcrImages) {
        if ((Invoke-Native { docker pull "$ImageMirror/$img" }) -ne 0) {
            Warn "镜像站拉取失败：$ImageMirror/$img"
            return $false
        }
        if ((Invoke-Native { docker tag "$ImageMirror/$img" "ghcr.io/$img" }) -ne 0) {
            Warn "镜像打标失败：$img"
            return $false
        }
    }
    # ghcr 镜像已由镜像站补齐，compose pull 只需拉 postgres/neo4j/minio 等基础镜像
    return (Invoke-ComposePull)
}

Info '正在拉取镜像（首次约 2-3 分钟，视网络而定）...'
$pulled = $false
for ($i = 1; $i -le 3; $i++) {
    if (Invoke-ComposePull) { $pulled = $true; break }
    Warn "镜像拉取失败（第 $i/3 次，网络中断会自动续传），重试..."
}
if (-not $pulled -and (Install-ImagesViaMirror)) { $pulled = $true }
if (-not $pulled) {
    Warn '多次拉取失败。国内网络拉取 Docker Hub 镜像通常需要配置镜像加速器。'
    $answer = Read-Host '是否自动配置国内镜像加速器（写入 ~/.docker/daemon.json 并重启 Docker）？(Y/n)'
    if ($answer -notmatch '^[nN]') {
        $daemonJson = Join-Path $env:USERPROFILE '.docker\daemon.json'
        New-Item -ItemType Directory -Force -Path (Split-Path $daemonJson) | Out-Null
        $daemon = @{ 'registry-mirrors' = @('https://docker.m.daocloud.io', 'https://docker.1ms.run') }
        if (Test-Path $daemonJson) {
            try { $daemon = (Get-Content $daemonJson -Raw | ConvertFrom-Json) } catch {}
            if (-not $daemon.'registry-mirrors') {
                $daemon | Add-Member -NotePropertyName 'registry-mirrors' -NotePropertyValue @('https://docker.m.daocloud.io', 'https://docker.1ms.run') -Force
            }
        }
        [System.IO.File]::WriteAllText($daemonJson, ($daemon | ConvertTo-Json -Depth 5), [System.Text.UTF8Encoding]::new($false))
        Info '已写入加速器，重启 Docker Desktop...'
        Get-Process | Where-Object { $_.ProcessName -like '*docker*' } | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 5
        Start-Process 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
        $waited = 0
        while (-not (Test-DockerEngine)) {
            Start-Sleep -Seconds 5
            $waited += 5
            if ($waited -ge 240) { Fatal 'Docker 重启后引擎未就绪，请打开 Docker Desktop 确认状态后重试' }
        }
        for ($i = 1; $i -le 3; $i++) {
            if (Invoke-ComposePull) { $pulled = $true; break }
            Warn "镜像拉取失败（第 $i/3 次），重试..."
        }
    }
    if (-not $pulled) { Fatal '镜像拉取仍失败。也可 clone 仓库后用 docker compose up --build -d 本地构建（约 20 分钟）' }
}

Info '启动服务...'
if ((Invoke-Native { docker compose up -d }) -ne 0) { Fatal '服务启动失败。查看日志：docker compose logs api' }

# --- 7. 健康检查 -----------------------------------------------------------------
Info '等待 API 就绪...'
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        Invoke-WebRequest -Uri 'http://localhost:8000/docs' -UseBasicParsing -TimeoutSec 3 | Out-Null
        $ready = $true
        break
    } catch { Start-Sleep -Seconds 2 }
}
if (-not $ready) { Fatal 'API 120 秒内未就绪，请运行 docker compose logs api 排查' }

# --- 8. 完成 ---------------------------------------------------------------------
Write-Host ''
Write-Host '============================================================'
Write-Host '  Augura 已启动！'
Write-Host '  Web:  http://localhost:3000'
Write-Host ''
Write-Host '  首次使用: Settings 配置 AI Key -> Upload 上传素材'
Write-Host "  目录:   $HomeDir（重复运行本脚本即更新）"
Write-Host '============================================================'
Start-Process 'http://localhost:3000'
