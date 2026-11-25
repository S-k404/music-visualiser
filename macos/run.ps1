<#
.SYNOPSIS
    Run ASCII Music Visualiser on Windows 11.
.DESCRIPTION
    Checks ffmpeg (installable via winget), creates macos\.venv on first run,
    installs Python dependencies, and starts ascii_visualizer.
#>
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "Note: ffmpeg not found on PATH. Installing via winget..." -ForegroundColor Yellow
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id Gyan.FFmpeg --exact --accept-package-agreements --accept-source-agreements
    } else {
        Write-Host "Install ffmpeg manually (winget install Gyan.FFmpeg) to decode mp3/m4a/aac/wma/opus." -ForegroundColor Yellow
    }
}

$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "Creating virtual environment in macos\.venv..."
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py -3 -m venv .venv
    } else {
        python -m venv .venv
    }
    & $venvPy -m pip install --upgrade pip --quiet
    & $venvPy -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
}

& $venvPy -m ascii_visualizer @args
