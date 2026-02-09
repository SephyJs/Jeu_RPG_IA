$ErrorActionPreference = 'Stop'

if (-not (Test-Path '.\.venv')) {
  Write-Error "Missing .venv. Create it with: python -m venv .venv"
  exit 1
}

. .\.venv\Scripts\Activate.ps1
python app/main.py