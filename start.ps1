# Starts Early Warning Studio and opens it in the browser.
#   .\start.ps1             start the app
#   .\start.ps1 -Install    install the Python packages first
param([switch]$Install)

Set-Location $PSScriptRoot
if ($Install) { python -m pip install -r requirements.txt }
if (-not (Test-Path .env)) { Write-Host "No .env file: AI commentary will use template text. Copy .env.example to .env and add ANTHROPIC_API_KEY." -ForegroundColor Yellow }

$env:PYTHONUTF8 = "1"
$port = if ($env:EWS_PORT) { $env:EWS_PORT } else { "8765" }
Start-Job -ScriptBlock { param($p) Start-Sleep -Seconds 4; Start-Process "http://127.0.0.1:$p" } -ArgumentList $port | Out-Null
python -m server
