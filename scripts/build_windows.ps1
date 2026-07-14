# Build windowed GUI + Windows installer
# Usage: powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating venv..."
    python -m venv .venv
    $VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
}

& $VenvPython -m pip install -q -e . pyinstaller

$Dist = Join-Path $Root "dist"
$Build = Join-Path $Root "build"
Remove-Item $Dist -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $Build -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "Running PyInstaller (windowed, no console)..."
& $VenvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name "GrokAccountManager" `
    --paths (Join-Path $Root "src") `
    --collect-all customtkinter `
    --collect-all darkdetect `
    --hidden-import PIL `
    --hidden-import PIL._tkinter_finder `
    --hidden-import psutil `
    (Join-Path $Root "src\grok_account_manager\__main__.py")

$AppDir = Join-Path $Dist "GrokAccountManager"
if (-not (Test-Path (Join-Path $AppDir "GrokAccountManager.exe"))) {
    throw "Build failed: GrokAccountManager.exe not found"
}

# Portable zip
$Zip = Join-Path $Dist "GrokAccountManager-Portable.zip"
if (Test-Path $Zip) { Remove-Item $Zip -Force }
Compress-Archive -Path (Join-Path $AppDir "*") -DestinationPath $Zip -Force
Write-Host "Portable zip: $Zip"

# Inno Setup if available
$Iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
    "${env:LocalAppData}\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($Iscc) {
    Write-Host "Building installer with Inno Setup: $Iscc"
    & $Iscc (Join-Path $Root "installer\GrokAccountManager.iss")
    Write-Host "Installer output under dist\"
} else {
    Write-Host "Inno Setup not found — portable zip only. Install Inno Setup 6 to build Setup.exe."
}

Write-Host "Done."
