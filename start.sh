#!/usr/bin/env bash
set -euo pipefail

if [ ! -d ".venv" ]; then
  echo "Missing .venv. Create it with: python3 -m venv .venv" >&2
  exit 1
fi

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then
  PYTHON=".venv/Scripts/python.exe"
elif [ -x ".venv/Scripts/python" ]; then
  PYTHON=".venv/Scripts/python"
else
  echo "Could not find a Python interpreter in .venv." >&2
  exit 1
fi

server_pid=""
bot_pid=""

cleanup() {
  if [ -n "${server_pid}" ] && kill -0 "${server_pid}" 2>/dev/null; then
    kill "${server_pid}" 2>/dev/null || true
  fi
  if [ -n "${bot_pid}" ] && kill -0 "${bot_pid}" 2>/dev/null; then
    kill "${bot_pid}" 2>/dev/null || true
  fi
}

trap cleanup INT TERM EXIT

"${PYTHON}" -m app.main &
server_pid=$!
echo "Server started (pid ${server_pid})"

"${PYTHON}" -m app.telegram.bot &
bot_pid=$!
echo "Telegram bot started (pid ${bot_pid})"

set +e
wait -n "${server_pid}" "${bot_pid}"
status=$?
set -e

if kill -0 "${server_pid}" 2>/dev/null; then
  echo "Telegram bot stopped (exit ${status}), stopping server..."
else
  echo "Server stopped (exit ${status}), stopping Telegram bot..."
fi

cleanup
wait "${server_pid}" 2>/dev/null || true
wait "${bot_pid}" 2>/dev/null || true
exit "${status}"
