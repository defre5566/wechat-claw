# wechat-claw Windows clean install script (one-shot)
#   - Removes service/registry/shortcuts/old deployment, re-clones, installs deps, starts wizard.
#   - Keeps: Python, Git, PAT (credential manager).
# Usage:
#   powershell -ExecutionPolicy Bypass -File clean-install.ps1 [-Path C:\wechat-claw-private] [-CleanOpencode]
#   -Path          deploy dir (default C:\wechat-claw-private)
#   -CleanOpencode also remove ~\.opencode (wizard will reinstall it)
param(
  [string]$Path = "C:\wechat-claw-private",
  [string]$Repo = "https://github.com/defre5566/wechat-claw-private.git",
  [switch]$CleanOpencode
)
$ErrorActionPreference = "Stop"

function Step($m) { Write-Host "== $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [WARN] $m" -ForegroundColor Yellow }

Step "1/7 Stop wechat-claw processes..."
Get-Process python -ErrorAction SilentlyContinue |
  Where-Object { $_.Path -like "*wechat*" -or $_.MainWindowTitle -like "*wechat*" } |
  Stop-Process -Force -ErrorAction SilentlyContinue
Stop-Process -Name wechat-claw -Force -ErrorAction SilentlyContinue

Step "2/7 Remove nssm service (needs admin)..."
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).
  IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($isAdmin) {
  nssm stop wechat-bridge 2>$null | Out-Null
  nssm remove wechat-bridge confirm 2>$null | Out-Null
  Ok "nssm service removed"
} else {
  Warn "Not admin - service removal skipped. Run as admin once if a stale service exists."
}

Step "3/7 Clean registry entries..."
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "wechat-claw-bridge" /f 2>$null | Out-Null
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\wechat-claw" /f 2>$null | Out-Null
Ok "registry cleaned"

Step "4/7 Remove old deployment files..."
Remove-Item "$env:LOCALAPPDATA\wechat-claw" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\wechat-claw" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $Path -Recurse -Force -ErrorAction SilentlyContinue
if ($CleanOpencode) { Remove-Item "$env:USERPROFILE\.opencode" -Recurse -Force -ErrorAction SilentlyContinue }
Ok "files cleaned"

Step "5/7 Clone repository..."
git clone $Repo $Path
if ($LASTEXITCODE -ne 0) { throw "git clone failed - check PAT (username defre5566 + PAT as password) and network" }
Ok "cloned"

Step "6/7 Create venv and install dependencies..."
Push-Location $Path
python -m venv .venv
& ".\.venv\Scripts\pip.exe" install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "pip install requirements failed" }
& ".\.venv\Scripts\pip.exe" install -e ".\vendor\wechat_agent_sdk"
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "pip install sdk failed" }
& ".\.venv\Scripts\python.exe" patches\apply_patches.py --vendor --check-only
Pop-Location
Ok "dependencies ready (expect 4x [SKIP] above)"

Step "7/7 Start web wizard..."
Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "`"$Path\web\start.bat`"" -WorkingDirectory $Path
Ok "Wizard started - open http://127.0.0.1:8650/ in browser"

Write-Host ""
Write-Host "Done. Walk the wizard, then verify: opencode live status / QR auto-fetch /" -ForegroundColor Green
Write-Host "city picker / persona optimize / shortcut auto-created / module source signed / autostart toggle." -ForegroundColor Green
