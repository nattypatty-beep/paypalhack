# MandatePay - one-command local start for Windows.
# Usage:  powershell -ExecutionPolicy Bypass -File .\start.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path ".venv")) {
    Write-Host "Creating .venv ..."
    python -m venv .venv
}

$python = Join-Path $root ".venv\Scripts\python.exe"
& $python -m pip install --disable-pip-version-check -q --upgrade pip
& $python -m pip install --disable-pip-version-check -q -r requirements.txt

Write-Host ""
Write-Host "MandatePay is starting at http://127.0.0.1:8000"
Write-Host "Mock PayPal + rules engines are used unless you add credentials to .env"
Write-Host "Press Ctrl+C to stop."
Write-Host ""

& $python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
