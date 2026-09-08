$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# The remote-support runtime is optional.  This script is only run when the
# remote-support feature is being prepared; Online itself does not import the
# RTC dependencies while the switch is off.
if (-not (Test-Path ".venv\\Scripts\\python.exe")) {
  python -m venv .venv
}

$python = Join-Path $PSScriptRoot ".venv\\Scripts\\python.exe"
& $python -m pip install --upgrade pip
& $python -m pip install -r (Join-Path $PSScriptRoot "requirements.txt") pyinstaller
if ($LASTEXITCODE -ne 0) { throw "Unable to install remote-support dependencies" }

# Mark the isolated source runtime ready only after all imports/build tools
# are available.  The Online backend checks this marker only after the user
# enables remote support.
& $python -c "import aiortc, av, mss, numpy, PIL, pyautogui, pyperclip, websocket"
if ($LASTEXITCODE -ne 0) { throw "Remote-support dependency import check failed" }
Set-Content -Path (Join-Path $PSScriptRoot ".ready") -Value "ready" -Encoding ascii

& (Join-Path $PSScriptRoot ".venv\\Scripts\\pyinstaller.exe") --clean --onefile --windowed --name BHZN-ToDesk-Agent (Join-Path $PSScriptRoot "bhzn_desktop_agent.py")
if ($LASTEXITCODE -ne 0) { throw "Remote-support agent build failed" }
Write-Host "Built source agent: $PSScriptRoot\\dist\\BHZN-ToDesk-Agent.exe"
