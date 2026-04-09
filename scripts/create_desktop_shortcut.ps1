#Requires -Version 5.1
# 安装完成后：在桌面创建快捷方式；图标与名称来自 static\branding\brands.json 中 marks.<LOBSTER_BRAND_MARK>.install
# 退出码：0=已创建 1=失败（未知标记等） 2=跳过（缺文件等）
param(
    [Parameter(Mandatory = $true)]
    [string]$Root,
    [string]$BrandMark = ''
)

$ErrorActionPreference = 'Stop'
$Root = $Root.TrimEnd('\', '/')
$jsonPath = Join-Path $Root 'static\branding\brands.json'
$bat = Join-Path $Root 'start.bat'

if (-not (Test-Path -LiteralPath $bat)) {
    Write-Host "[desktop-shortcut] start.bat not found, skip."
    exit 2
}
if (-not (Test-Path -LiteralPath $jsonPath)) {
    Write-Host "[desktop-shortcut] static\branding\brands.json not found, skip."
    exit 2
}

$json = Get-Content -LiteralPath $jsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
$m = $BrandMark.Trim()
if ([string]::IsNullOrWhiteSpace($m)) {
    $m = [string]$json.default_mark
}
if ([string]::IsNullOrWhiteSpace($m)) {
    Write-Host "[desktop-shortcut] BrandMark and default_mark empty."
    exit 1
}

$b = $json.marks.$m
if ($null -eq $b) {
    Write-Host "[desktop-shortcut] unknown brand mark: $m"
    exit 1
}

$inst = $b.install
if ($null -eq $inst) {
    Write-Host "[desktop-shortcut] install block missing for mark: $m"
    exit 1
}

$icoRel = [string]$inst.desktop_ico
if ([string]::IsNullOrWhiteSpace($icoRel)) {
    Write-Host "[desktop-shortcut] desktop_ico missing for mark: $m"
    exit 1
}
$ico = Join-Path $Root ($icoRel -replace '/', '\')

if (-not (Test-Path -LiteralPath $ico)) {
    Write-Host "[desktop-shortcut] icon not found: $ico"
    exit 2
}

$lnkName = [string]$inst.shortcut_lnk_name
if ([string]::IsNullOrWhiteSpace($lnkName)) {
    Write-Host "[desktop-shortcut] shortcut_lnk_name missing for mark: $m"
    exit 1
}

function Get-UserDesktopDir {
    $d = [Environment]::GetFolderPath('Desktop')
    if (-not [string]::IsNullOrWhiteSpace($d) -and (Test-Path -LiteralPath $d)) { return $d }
    $d = Join-Path $env:USERPROFILE 'Desktop'
    if (Test-Path -LiteralPath $d) { return $d }
    $d = Join-Path $env:USERPROFILE 'OneDrive\Desktop'
    if (Test-Path -LiteralPath $d) { return $d }
    return $null
}

$desktop = Get-UserDesktopDir
if ([string]::IsNullOrWhiteSpace($desktop)) {
    Write-Host "[desktop-shortcut] Desktop folder not found, skip."
    exit 2
}

$desc = [string]$inst.shortcut_description
if ([string]::IsNullOrWhiteSpace($desc)) { $desc = 'Lobster local server' }

$lnkPath = Join-Path $desktop $lnkName
try {
    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($lnkPath)
    $sc.TargetPath = $bat
    $sc.WorkingDirectory = $Root
    $sc.IconLocation = "$ico,0"
    $sc.Description = $desc
    $sc.Save()
}
catch {
    Write-Host "[desktop-shortcut] ERROR: $($_.Exception.Message)"
    exit 1
}

Write-Host "[desktop-shortcut] OK: $lnkPath (mark=$m)"
exit 0
