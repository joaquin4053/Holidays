#!/bin/zsh
# Start backend (port 8000) and frontend (port 5173). Ctrl+C stops both.
cd "$(dirname "$0")"
export PATH="$HOME/.local/node/bin:$PATH"
(cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000) &
(cd frontend && npm run dev) &
trap 'kill 0' INT TERM
wait
