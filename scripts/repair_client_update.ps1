#Requires -Version 5.1
param(
    [string]$InstallRoot = ''
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms

$exeName = -join ([char]0x5fc5, [char]0x706b, [char]0x667a, [char]0x80fd, 'AI', '.exe')
$packageRoot = Split-Path -Parent $PSScriptRoot
$bootstrapExe = Join-Path $packageRoot $exeName

if (-not (Test-Path -LiteralPath $bootstrapExe -PathType Leaf)) {
    [System.Windows.Forms.MessageBox]::Show("The repair package is missing its launcher: $bootstrapExe", 'Bihuo Update Repair') | Out-Null
    exit 1
}

function Test-ClientRoot([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    return (Test-Path -LiteralPath (Join-Path $Path 'desktop\launcher.py') -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $Path 'backend') -PathType Container)
}

function Find-ShortcutClientRoot {
    $desktopDirs = @(
        [Environment]::GetFolderPath('Desktop'),
        [Environment]::GetFolderPath('CommonDesktopDirectory'),
        (Join-Path $env:USERPROFILE 'Desktop'),
        (Join-Path $env:USERPROFILE 'OneDrive\Desktop')
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Container) } | Select-Object -Unique

    $shell = New-Object -ComObject WScript.Shell
    $links = foreach ($desktop in $desktopDirs) {
        Get-ChildItem -LiteralPath $desktop -Filter '*.lnk' -File -ErrorAction SilentlyContinue
    }
    foreach ($link in ($links | Sort-Object LastWriteTime -Descending)) {
        try {
            $shortcut = $shell.CreateShortcut($link.FullName)
            $target = [string]$shortcut.TargetPath
            if ([string]::IsNullOrWhiteSpace($target)) { continue }
            $root = Split-Path -Parent $target
            if (Test-ClientRoot $root) {
                return [pscustomobject]@{ Root = $root; Link = $link.FullName }
            }
        }
        catch {}
    }
    return $null
}

$shortcutInfo = $null
if ([string]::IsNullOrWhiteSpace($InstallRoot) -and (Test-ClientRoot $packageRoot)) {
    $InstallRoot = $packageRoot
}
if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    $shortcutInfo = Find-ShortcutClientRoot
    if ($null -ne $shortcutInfo) {
        $InstallRoot = [string]$shortcutInfo.Root
    }
}
if (-not (Test-ClientRoot $InstallRoot)) {
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = 'Select the Bihuo client installation folder'
    $dialog.ShowNewFolderButton = $false
    if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
        exit 2
    }
    $InstallRoot = $dialog.SelectedPath
}
if (-not (Test-ClientRoot $InstallRoot)) {
    [System.Windows.Forms.MessageBox]::Show('The selected folder is not a valid Bihuo client installation.', 'Bihuo Update Repair') | Out-Null
    exit 1
}

$targetExe = Join-Path $InstallRoot $exeName
if (-not [string]::Equals($bootstrapExe, $targetExe, [StringComparison]::OrdinalIgnoreCase)) {
    Copy-Item -LiteralPath $bootstrapExe -Destination $targetExe -Force
}

[Environment]::SetEnvironmentVariable('LOBSTER_ENABLE_DEV_CODE_UPDATE', '1', 'User')
$env:LOBSTER_ENABLE_DEV_CODE_UPDATE = '1'

if ($null -ne $shortcutInfo -and (Test-Path -LiteralPath $shortcutInfo.Link -PathType Leaf)) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutInfo.Link)
    $shortcut.TargetPath = $targetExe
    $shortcut.WorkingDirectory = $InstallRoot
    $shortcut.Save()
}

$shortcutInstaller = Join-Path $InstallRoot 'scripts\create_desktop_shortcut.ps1'
if (Test-Path -LiteralPath $shortcutInstaller -PathType Leaf) {
    try {
        & $shortcutInstaller -Root $InstallRoot | Out-Null
    }
    catch {}
}

Start-Process -FilePath $targetExe -WorkingDirectory $InstallRoot
[System.Windows.Forms.MessageBox]::Show(
    'Update repair completed. The client is restarting and checking for the latest version.',
    'Bihuo Update Repair'
) | Out-Null
