#!/usr/bin/env bash
set -euo pipefail

if [ ! -d ".venv" ]; then
  echo "Missing .venv. Create it with: python3 -m venv .venv" >&2
  exit 1
fi

source .venv/Scripts/activate

if command -v python3 >/dev/null 2>&1; then
  python3 app/main.py
else
  python app/main.py
fi