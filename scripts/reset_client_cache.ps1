<#
清缓存并重启客户端（用于「改了不生效 / 版本不更新 / 页面还是老样式」的机器）

用法（在该机器的 lobster_online 目录下）：
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\reset_client_cache.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\reset_client_cache.ps1 -NoRestart

做四件事：
 1. 关掉 launcher / backend / webview / 工作进程
 2. 清 WebView2 缓存（保留登录态 Cookies / Local Storage）
 3. 清 __pycache__
 4. 重新启动客户端，并打印本机代码版本
#>
param([switch]$NoRestart)

$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $root "backend"))) {
    Write-Host "[ERR] 找不到 backend 目录：$root" -ForegroundColor Red
    exit 1
}
$rootRe = [regex]::Escape($root)

Write-Host "[1/4] 关闭客户端进程"
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and ($_.CommandLine -match $rootRe) -and
    ($_.CommandLine -match "launcher\.py|backend[\\/]run\.py|mcp|aihypit|rotating_log_runner|ensure-npm")
} | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }
Get-Process msedgewebview2 | ForEach-Object { try { Stop-Process -Id $_.Id -Force -ErrorAction Stop } catch {} }
Start-Sleep -Seconds 3

$cacheRoots = @(
    (Join-Path $root "browser_data\desktop_webview\EBWebView\Default\Cache"),
    (Join-Path $root "browser_data\desktop_webview\EBWebView\Default\Code Cache"),
    (Join-Path $root "browser_data\desktop_webview\EBWebView\Default\GPUCache"),
    (Join-Path $root "browser_data\desktop_webview\EBWebView\Default\DawnGraphiteCache"),
    (Join-Path $root "browser_data\desktop_webview\EBWebView\Default\DawnWebGPUCache"),
    (Join-Path $root "browser_data\desktop_webview\EBWebView\GraphiteDawnCache"),
    (Join-Path $root "browser_data\desktop_webview\EBWebView\GrShaderCache"),
    (Join-Path $root "browser_data\desktop_webview\EBWebView\ShaderCache")
)
Write-Host "[2/4] 清 WebView2 缓存（保留登录态）"
foreach ($p in $cacheRoots) {
    if (Test-Path -LiteralPath $p) {
        try { Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction Stop; Write-Host "  cleared $p" }
        catch { Write-Host "  skip  $p ($($_.Exception.Message))" }
    }
}

Write-Host "[3/4] 清 __pycache__"
Get-ChildItem -Path $root -Recurse -Directory -Filter "__pycache__" |
    Where-Object { $_.FullName -notmatch "\\python\\|\\node_modules\\|\\.git\\" } |
    ForEach-Object { try { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction Stop } catch {} }

$vf = Join-Path $root "static\client_version.json"
if (Test-Path $vf) {
    try {
        $v = Get-Content $vf -Raw | ConvertFrom-Json
        Write-Host ("[version] 本机代码版本 = {0} / build {1} ({2})" -f $v.version, $v.build, $v.applied_at)
    } catch {}
}

if ($NoRestart) {
    Write-Host "[4/4] 已跳过重启（-NoRestart）"
    exit 0
}
Write-Host "[4/4] 重新启动客户端"
$pyw = Join-Path $root "python\pythonw.exe"
$launcher = Join-Path $root "desktop\launcher.py"
if ((Test-Path $pyw) -and (Test-Path $launcher)) {
    Start-Process -FilePath $pyw -ArgumentList $launcher -WorkingDirectory $root -WindowStyle Hidden
    Start-Sleep -Seconds 12
    $listening = (Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -eq 8000 }).Count
    Write-Host "  port 8000 listening = $listening"
} else {
    Write-Host "  找不到 pythonw/launcher，请手动启动客户端" -ForegroundColor Yellow
}